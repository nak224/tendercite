"""Synthetic HTTP coverage for explicit, read-only capability assessment reports."""

import asyncio
import json
import socket
import sqlite3
from contextlib import contextmanager
from unittest.mock import Mock

import httpx
import pytest
from capability_helpers import example
from fastapi import HTTPException
from matching_helpers import SKILL_A, SKILL_B, analysis, finding, snapshot
from persistent_mapping_helpers import (
    approval_input,
    approved_mapping,
    draft_input,
    revocation_input,
    seeded_repository,
)

from tendercite.api.dependencies import get_read_only_repository, get_repository
from tendercite.api.routes import capability_reports
from tendercite.core.config import Settings, settings
from tendercite.domain.capability_workflow import AssessmentReport, AssessmentSelection
from tendercite.domain.models import AssessmentRequest, GroundingStatus, ReviewedValue, ReviewStatus
from tendercite.domain.review import ReviewRequest
from tendercite.main import app
from tendercite.repositories.sqlite import SqliteRepository
from tendercite.services.capability_workflow import assessment_report

REPORT = "/api/v1/capability-workflow/report"
MAX_BYTES = 65536


@pytest.fixture
def enabled(client, tmp_path, monkeypatch):
    repo = seeded_repository(tmp_path / "tendercite.db")
    monkeypatch.setattr(settings, "capability_report_api_enabled", True)
    monkeypatch.setattr(settings, "capability_mapping_api_enabled", False)
    # The report must use its read-only dependency, never the initializing shared one.
    app.dependency_overrides[get_repository] = lambda: pytest.fail("Writable repository opened")
    return client, repo


def selection(**changes):
    return {
        "schema_version": "capability-workflow-assessment-selection-1",
        "analysis_id": "analysis-1",
        "snapshot_hash": snapshot().content_hash,
        "finding_ids": ["finding-1"],
        **changes,
    }


def post(client, **changes):
    return client.post(REPORT, json=selection(**changes))


def assert_safe(response, status, code):
    assert response.status_code == status
    assert response.json() == {"detail": {"code": code}}
    assert response.headers["cache-control"] == "no-store"
    for private in ("PRIVATE", "person_id", "workforce_members", "database_path", "Traceback"):
        assert private not in response.text


@pytest.mark.parametrize("mapping_enabled", [False, True])
def test_report_disabled_by_default_independently_of_mapping_flag(
    client, tmp_path, monkeypatch, mapping_enabled
):
    monkeypatch.delenv("TENDERCITE_CAPABILITY_REPORT_API_ENABLED", raising=False)
    assert Settings(_env_file=None).capability_report_api_enabled is False
    monkeypatch.setattr(settings, "capability_report_api_enabled", False)
    monkeypatch.setattr(settings, "capability_mapping_api_enabled", mapping_enabled)
    absent = tmp_path / "absent"
    monkeypatch.setattr(settings, "data_dir", absent)
    app.dependency_overrides[get_read_only_repository] = lambda: pytest.fail("Disabled DB access")
    assert_safe(
        client.post(REPORT, content=b"PRIVATE", headers={"Content-Type": "application/json"}),
        404,
        "CAPABILITY_REPORT_API_DISABLED",
    )
    assert not absent.exists()


def test_report_matches_existing_versioned_service_and_openapi(enabled):
    client, repo = enabled
    response = post(client)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    report = AssessmentReport.model_validate_json(response.content)
    assert report == assessment_report(
        AssessmentSelection.model_validate_json(json.dumps(selection())), repository=repo
    )
    assert report.schema_version == "capability-workflow-report-1"
    assert report.matching_contract_version == "capability-matching-result-1"
    assert report.algorithm_version == "explicit-uuid-membership-1"
    operation = app.openapi()["paths"][REPORT]["post"]
    assert "AssessmentSelection" in str(operation["requestBody"])
    assert "AssessmentReport" in str(operation["responses"]["200"])


def test_report_opt_in_does_not_enable_mapping_api(enabled):
    client, _ = enabled
    assert post(client).status_code == 200
    assert_safe(
        client.get("/api/v1/capability-workflow/overview"),
        404,
        "CAPABILITY_MAPPING_API_DISABLED",
    )


def test_current_approval_preserves_original_effective_sources_and_evidence_boundaries(enabled):
    client, repo = enabled
    original = repo.get_finding("finding-1")
    repo.add_review(
        "finding-1",
        ReviewRequest(
            action="MODIFY",
            reviewed_value=ReviewedValue(
                statement="Five years of PostgreSQL experience",
                category="MUST",
                requirement_type="TECHNICAL",
            ),
        ),
    )
    draft = repo.create_capability_mapping_draft(draft_input(repo, skill_ids=(SKILL_A, SKILL_B)))
    approved = repo.approve_capability_mapping(approval_input(draft))
    response = post(client)
    assert response.status_code == 200
    report = AssessmentReport.model_validate_json(response.content)
    row = report.findings[0]
    assert report.analysis_id == "analysis-1"
    assert report.snapshot_hash == report.snapshot.content_hash == snapshot().content_hash
    assert report.snapshot.assessment_period == snapshot().assessment_period
    assert row.finding.finding_id == "finding-1"
    assert row.finding.original_requirement.statement == original.statement
    assert row.finding.effective_requirement.statement == "Five years of PostgreSQL experience"
    assert row.finding.source_references == tuple(original.evidence)
    assert row.finding.review_status == "MODIFIED"
    assert row.finding.grounding_status == "VERIFIED_QUOTE"
    assert row.finding.mapping.mapping_id == approved.mapping_id
    assert row.finding.mapping.revision == 2
    assert row.finding.mapping.skill_ids == (SKILL_A, SKILL_B)
    assert row.coverage == "ALL_REQUIRED_SKILLS_RECORDED"
    assert row.individual_counts.model_dump() == dict(
        all_required_recorded=1, some_required_recorded=1, no_required_recorded=1
    )
    assert [r.coverage.value for r in row.individual_coverage] == [
        "ALL_REQUIRED_SKILLS_RECORDED",
        "SOME_REQUIRED_SKILLS_RECORDED",
        "NO_REQUIRED_SKILLS_RECORDED",
    ]
    assert report.purpose == "ASSESSMENT_SUPPORT_ONLY"
    assert report.qualification == row.qualification == "NOT_VERIFIED"
    assert report.capacity_evaluation == row.capacity_evaluation == "NOT_EVALUATED"
    assert row.reviewed_meaning_verification == "NOT_ASSESSED"
    assert {item.code.value for item in row.limitations} >= {
        "ASSIGNMENTS_NOT_VERIFIED_QUALIFICATION",
        "PROFICIENCY_NOT_EXPERIENCE_DURATION",
        "CERTIFICATIONS_AND_REFERENCES_NOT_VERIFIED",
        "CAPACITY_AND_COMMITMENT_NOT_EVALUATED",
        "CHANGED_REVIEWED_MEANING_NOT_SOURCE_VERIFIED",
    }
    assert "Go/No-Go are not evaluated" in " ".join(report.evidence_boundaries)
    for absent in ("capacity_state", "recorded_assignments", "email", "fit_score", "verdict"):
        assert f'"{absent}":' not in response.text


@pytest.mark.parametrize(
    "state,reason",
    [
        ("NOT_FOUND", "NO_EXPLICIT_SKILL_MAPPING"),
        ("DRAFT", "PERSISTED_MAPPING_NOT_APPROVED"),
        ("STALE", "PERSISTED_MAPPING_STALE"),
        ("REVOKED", "PERSISTED_MAPPING_REVOKED"),
    ],
)
def test_unusable_mapping_states_remain_visible_not_evaluated(enabled, state, reason):
    client, repo = enabled
    if state == "DRAFT":
        repo.create_capability_mapping_draft(draft_input(repo))
    elif state in ("STALE", "REVOKED"):
        record = approved_mapping(repo)
        if state == "STALE":
            repo.add_review("finding-1", ReviewRequest(action="CONFIRM"))
        else:
            repo.revoke_capability_mapping(revocation_input(record))
    before = repo.database_path.read_bytes()
    row = post(client).json()["findings"][0]
    assert row["finding"]["mapping"]["status"] == state
    assert row["coverage"] == "NOT_EVALUATED"
    assert reason in row["non_evaluation_reasons"]
    assert row["individual_coverage"] == []
    assert repo.database_path.read_bytes() == before


@pytest.mark.parametrize(
    "state,reason",
    [
        ("UNREVIEWED", "UNREVIEWED_FINDING"),
        ("REJECTED", "REJECTED_FINDING"),
        ("INVALID_QUOTE", "INVALID_SOURCE_QUOTE"),
        ("MISSING_EVIDENCE", "MISSING_SOURCE_EVIDENCE"),
    ],
)
def test_ineligible_findings_are_explicitly_not_evaluated(
    enabled, tmp_path, monkeypatch, state, reason
):
    client, _ = enabled
    value = finding(review_status=ReviewStatus.UNREVIEWED)
    if state == "INVALID_QUOTE":
        value.evidence[0].grounding_status = GroundingStatus.INVALID_QUOTE
    elif state == "MISSING_EVIDENCE":
        value.evidence = []
    directory = tmp_path / state
    repo = seeded_repository(
        directory / "tendercite.db", confirm=state != "UNREVIEWED", findings=[value]
    )
    if state == "REJECTED":
        repo.add_review(value.id, ReviewRequest(action="REJECT"))
    monkeypatch.setattr(settings, "data_dir", directory)
    row = post(client).json()["findings"][0]
    assert row["coverage"] == "NOT_EVALUATED"
    assert reason in row["non_evaluation_reasons"]
    assert row["individual_coverage"] == []


def test_selection_is_exact_sorted_and_never_reuses_another_snapshot(enabled):
    client, repo = enabled
    other = finding("foreign", "analysis-2", review_status=ReviewStatus.UNREVIEWED)
    repo.save_analysis(analysis(other, analysis_id="analysis-2"))
    # An explicit subset never expands to the other finding from the same analysis.
    values = [
        finding(i, "selected-analysis", review_status=ReviewStatus.UNREVIEWED)
        for i in ("z", "a", "excluded")
    ]
    repo.save_analysis(analysis(*values, analysis_id="selected-analysis"))
    report = post(client, analysis_id="selected-analysis", finding_ids=["z", "a"]).json()
    assert [r["finding"]["finding_id"] for r in report["findings"]] == ["a", "z"]
    assert report["summary"]["selected_finding_count"] == 2
    assert_safe(
        post(client, analysis_id="selected-analysis", finding_ids=["a", "foreign"]),
        404,
        "INVALID_MAPPING_SCOPE",
    )
    assert_safe(
        post(client, analysis_id="selected-analysis", finding_ids=["missing"]),
        404,
        "INVALID_MAPPING_SCOPE",
    )
    # A valid snapshot with the same catalog is still a different scope.
    body = example()
    body["assessment_period"] = {"start_date": "2027-03-01", "end_date": "2027-03-07"}
    second = snapshot(body)
    repo.import_capability_snapshot(second)
    repo.add_review("a", ReviewRequest(action="CONFIRM"))
    draft = repo.create_capability_mapping_draft(
        draft_input(repo, analysis_id="selected-analysis", finding_id="a")
    )
    repo.approve_capability_mapping(approval_input(draft))
    report = post(
        client,
        analysis_id="selected-analysis",
        finding_ids=["a"],
        snapshot_hash=second.content_hash,
    ).json()
    assert report["snapshot_hash"] == second.content_hash
    assert report["snapshot"]["assessment_period"] == second.assessment_period.model_dump(
        mode="json"
    )
    assert report["findings"][0]["finding"]["mapping"]["status"] == "NOT_FOUND"
    assert report["findings"][0]["coverage"] == "NOT_EVALUATED"


@pytest.mark.parametrize("finding_ids", [[], ["finding-1"]])
def test_missing_scope_never_falls_back_even_for_empty_selection(enabled, finding_ids):
    client, _ = enabled
    assert_safe(
        post(client, finding_ids=finding_ids, analysis_id="missing"), 404, "INVALID_MAPPING_SCOPE"
    )
    assert_safe(
        post(client, finding_ids=finding_ids, snapshot_hash="sha256:" + "0" * 64),
        404,
        "SNAPSHOT_NOT_FOUND",
    )


def test_explicit_empty_selection_is_a_no_op_and_100_findings_are_supported(enabled):
    client, repo = enabled
    report = post(client, finding_ids=[]).json()
    assert report["findings"] == []
    assert all(count == 0 for count in report["summary"].values())
    values = [finding(str(i), "hundred", review_status=ReviewStatus.UNREVIEWED) for i in range(100)]
    repo.save_analysis(analysis(*values, analysis_id="hundred"))
    response = post(client, analysis_id="hundred", finding_ids=[value.id for value in values])
    assert response.status_code == 200
    assert response.json()["summary"]["selected_finding_count"] == 100


@pytest.mark.parametrize(
    "changes",
    [
        {"schema_version": "PRIVATE"},
        {"finding_ids": ["finding-1", "finding-1"]},
        {"finding_ids": [str(i) for i in range(101)]},
        {"finding_ids": None},
        {"finding_ids": "finding-1"},
        {"finding_ids": [1]},
        {"finding_ids": [" "]},
        {"analysis_id": 1},
        {"snapshot_hash": "PRIVATE"},
        {"database_path": "/PRIVATE"},
        {"url": "https://PRIVATE.invalid"},
    ],
)
def test_invalid_contract_is_sanitized(enabled, changes):
    client, repo = enabled
    before = repo.database_path.read_bytes()
    assert_safe(post(client, **changes), 422, "INVALID_REPORT_INPUT")
    assert repo.database_path.read_bytes() == before


def test_finding_selection_is_required(enabled):
    client, _ = enabled
    body = selection()
    del body["finding_ids"]
    assert_safe(client.post(REPORT, json=body), 422, "INVALID_REPORT_INPUT")


@pytest.mark.parametrize(
    "raw",
    [
        b'{"PRIVATE":',
        b'{"analysis_id":"PRIVATE","analysis_id":"PRIVATE"}',
        b'{"nested":{"PRIVATE":1,"PRIVATE":2}}',
        b'{"finding_ids":NaN}',
        b"\xff",
        b"[]",
    ],
)
def test_malformed_json_and_duplicate_keys_are_sanitized(enabled, raw):
    client, _ = enabled
    assert_safe(
        client.post(REPORT, content=raw, headers={"Content-Type": "application/json"}),
        422,
        "INVALID_REPORT_INPUT",
    )


@pytest.mark.parametrize(
    "headers,status,code",
    [
        ({"Content-Type": "text/plain"}, 415, "UNSUPPORTED_MEDIA_TYPE"),
        (
            {"Content-Type": "application/json", "Content-Encoding": "gzip"},
            415,
            "UNSUPPORTED_CONTENT_ENCODING",
        ),
        (
            {"Content-Type": "application/json", "Content-Length": "PRIVATE"},
            400,
            "INVALID_CONTENT_LENGTH",
        ),
        (
            {"Content-Type": "application/json", "Content-Length": "1"},
            400,
            "INVALID_CONTENT_LENGTH",
        ),
        (
            {"Content-Type": "application/json", "Content-Length": "9999999999999999999"},
            413,
            "REPORT_REQUEST_TOO_LARGE",
        ),
    ],
)
def test_transport_errors_are_safe(enabled, headers, status, code):
    client, _ = enabled
    assert_safe(client.post(REPORT, content=b"{}", headers=headers), status, code)


def test_bounded_body_and_no_query_selectors_before_repository_access(enabled):
    client, _ = enabled
    app.dependency_overrides[get_read_only_repository] = lambda: pytest.fail(
        "Invalid body DB access"
    )
    assert_safe(
        client.post(
            REPORT, content=b" " * (MAX_BYTES + 1), headers={"Content-Type": "application/json"}
        ),
        413,
        "REPORT_REQUEST_TOO_LARGE",
    )
    assert_safe(
        client.post(REPORT, params={"database": "PRIVATE"}, json=selection()),
        422,
        "INVALID_REPORT_INPUT",
    )


def test_streamed_body_stops_at_size_limit_without_content_length(enabled):
    messages = iter(
        [
            {"type": "http.request", "body": b" " * MAX_BYTES, "more_body": True},
            {"type": "http.request", "body": b" ", "more_body": True},
        ]
    )
    sent = []

    async def receive():
        try:
            return next(messages)
        except StopIteration:
            pytest.fail("Consumed input after crossing size limit")

    async def send(message):
        sent.append(message)

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": REPORT,
        "query_string": b"",
        "headers": [(b"content-type", b"application/json")],
        "server": ("testserver", 80),
        "client": ("testclient", 123),
    }
    asyncio.run(app(scope, receive, send))
    assert sent[0]["status"] == 413
    assert json.loads(sent[1]["body"]) == {"detail": {"code": "REPORT_REQUEST_TOO_LARGE"}}


def test_missing_database_returns_safe_error_without_creation(enabled, tmp_path, monkeypatch):
    client, _ = enabled
    absent = tmp_path / "absent"
    monkeypatch.setattr(settings, "data_dir", absent)
    assert_safe(post(client), 503, "DATABASE_FAILED")
    assert not absent.exists()


@pytest.mark.parametrize(
    "failure,status,code",
    [
        (sqlite3.OperationalError("PRIVATE database_path"), 503, "DATABASE_FAILED"),
        (RuntimeError("PRIVATE person_id"), 500, "CAPABILITY_REPORT_FAILED"),
        (HTTPException(500, "PRIVATE"), 500, "CAPABILITY_REPORT_FAILED"),
    ],
)
def test_unexpected_errors_never_expose_private_details(
    enabled, monkeypatch, failure, status, code
):
    client, _ = enabled
    monkeypatch.setattr(capability_reports, "assessment_report", Mock(side_effect=failure))
    assert_safe(post(client), status, code)


def test_storage_integrity_errors_are_safe_and_preserve_corrupt_record(enabled):
    client, repo = enabled
    with repo._connect() as conn:
        conn.execute("UPDATE capability_snapshots SET canonical_json='{}'")
    before = repo.database_path.read_bytes()
    assert_safe(post(client), 409, "STORAGE_INTEGRITY")
    assert repo.database_path.read_bytes() == before


def test_report_uses_one_read_only_transaction_without_mutations_or_external_access(
    enabled, monkeypatch
):
    client, repo = enabled
    approved_mapping(repo)
    repo.set_assessment(
        "finding-1", AssessmentRequest(status="PARTIAL", rationale="Synthetic judgment")
    )
    before = repo.database_path.read_bytes()
    matrix = repo.list_matrix("analysis-1")
    statements, connections = [], []
    read_only = get_read_only_repository()
    assert read_only.read_only is True
    assert read_only.database_path == repo.database_path
    connect = read_only._connect

    @contextmanager
    def traced():
        with connect() as conn:
            connections.append(conn)
            conn.set_trace_callback(statements.append)
            yield conn

    def forbidden(*_args, **_kwargs):
        pytest.fail("Report attempted writes, external communication or model access")

    from tendercite.services.llm.openai_compatible import OpenAICompatibleLLM
    from tendercite.services.retrieval.embeddings import SentenceTransformerEmbeddingProvider

    monkeypatch.setattr(read_only, "_connect", traced)
    app.dependency_overrides[get_read_only_repository] = lambda: read_only
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", forbidden)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", forbidden)
    monkeypatch.setattr(OpenAICompatibleLLM, "request_completion", forbidden)
    monkeypatch.setattr(SentenceTransformerEmbeddingProvider, "__init__", forbidden)
    monkeypatch.setattr(SqliteRepository, "_initialize", forbidden)
    assert post(client).status_code == 200
    assert len(connections) == 1 and statements.count("BEGIN") == 1
    assert not any(s.startswith(("INSERT", "UPDATE", "DELETE", "CREATE")) for s in statements)
    assert repo.database_path.read_bytes() == before
    assert repo.list_matrix("analysis-1") == matrix
