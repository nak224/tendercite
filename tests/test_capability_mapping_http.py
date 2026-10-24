"""HTTP transport regressions for the synthetic, operator-driven mapping workflow."""

import json
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock
from uuid import UUID

import pytest
from matching_helpers import SKILL_B, finding, snapshot
from persistent_mapping_helpers import seeded_repository

from tendercite.api.dependencies import get_repository
from tendercite.core.config import Settings, settings
from tendercite.domain.models import GroundingStatus, ReviewStatus
from tendercite.domain.review import ReviewRequest
from tendercite.main import app

SNAPSHOTS = "/api/v1/capability-snapshots"
OVERVIEW = "/api/v1/capability-workflow/overview"
DRAFT = "/api/v1/capability-mappings/drafts"


@pytest.fixture
def enabled(client, tmp_path, monkeypatch):
    repository = seeded_repository(tmp_path / "manual-http.db")
    app.dependency_overrides[get_repository] = lambda: repository
    monkeypatch.setattr(settings, "capability_mapping_api_enabled", True)
    return client, repository


def params():
    return {"analysis_id": "analysis-1", "snapshot_hash": snapshot().content_hash}


def draft_body(**changes):
    return {
        "schema_version": "capability-workflow-mapping-selection-1",
        **params(),
        "finding_id": "finding-1",
        "operator": "ALL_OF",
        "skill_ids": [str(SKILL_B)],
        "rationale": "Explicit association to synthetic PostgreSQL",
        "operator_alias": "synthetic_operator",
        **changes,
    }


def assert_error(response, status, code):
    assert response.status_code == status
    assert response.json() == {"detail": {"code": code}}
    assert response.headers["cache-control"] == "no-store"
    for forbidden in ("PRIVATE", "person_id", "workforce_members", "database_path", "Traceback"):
        assert forbidden not in response.text


def get_overview(client):
    return client.get(OVERVIEW, params=params())


def create(client, body=None):
    return client.post(DRAFT, json=draft_body() if body is None else body)


def approve(client, record, **changes):
    body = {
        "schema_version": "capability-mapping-approval-1",
        "mapping_id": record["mapping_id"],
        "expected_revision": record["revision"],
        "expected_review_event_id": record["review_binding"]["review_event_id"],
        "expected_review_digest": record["review_binding"]["digest"],
        "rationale": "Explicit approval of manually selected skills",
        "operator_alias": "synthetic_operator",
        **changes,
    }
    return client.post(f"/api/v1/capability-mappings/{record['mapping_id']}/approve", json=body)


def test_disabled_by_default_without_repository_or_body_access(client, monkeypatch, tmp_path):
    monkeypatch.delenv("TENDERCITE_CAPABILITY_MAPPING_API_ENABLED", raising=False)
    assert Settings(_env_file=None).capability_mapping_api_enabled is False
    monkeypatch.setattr(settings, "capability_mapping_api_enabled", False)
    absent = tmp_path / "absent"
    monkeypatch.setattr(settings, "data_dir", absent)
    app.dependency_overrides[get_repository] = lambda: pytest.fail(
        "Disabled mapping touched SQLite"
    )
    for response in (
        client.get(SNAPSHOTS),
        client.get(OVERVIEW, params=params()),
        client.post(DRAFT, content=b"PRIVATE", headers={"Content-Type": "application/json"}),
        client.post(
            "/api/v1/capability-mappings/00000000-0000-0000-0000-000000000001/approve",
            content=b"PRIVATE",
            headers={"Content-Type": "application/json"},
        ),
    ):
        assert_error(response, 404, "CAPABILITY_MAPPING_API_DISABLED")
    assert not absent.exists()


def test_snapshot_listing_bounded_metadata_and_no_personnel(enabled):
    client, repo = enabled
    response = client.get(SNAPSHOTS, params={"limit": 1})
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json()["status"] == "LISTED"
    metadata = response.json()["snapshots"]
    assert len(metadata) == 1
    assert metadata[0]["content_hash"] == snapshot().content_hash
    assert metadata[0]["assessment_period_start"] == "2027-02-01"
    assert metadata[0]["assessment_period_end"] == "2027-02-07"
    assert "person_id" not in response.text and "skill_catalog" not in response.text
    for query in ({"limit": 0}, {"limit": 101}, {"limit": "PRIVATE"}, {"database": "PRIVATE"}):
        assert_error(client.get(SNAPSHOTS, params=query), 422, "INVALID_MAPPING_INPUT")
    assert repo.list_capability_snapshots()


def test_overview_reuses_existing_contract_is_consistent_and_read_only(enabled):
    client, repo = enabled
    before = repo.database_path.read_bytes()
    response = get_overview(client)
    assert response.status_code == 200
    result = response.json()
    assert result["schema_version"] == "capability-workflow-overview-1"
    assert result["analysis_id"] == "analysis-1"
    assert result["snapshot_hash"] == snapshot().content_hash
    assert result["snapshot"]["workforce_member_count"] == 3
    assert result["snapshot"]["skill_catalog"]
    finding = result["findings"][0]
    assert finding["finding_id"] == "finding-1"
    assert finding["mapping_preparation_eligible"] is True
    assert finding["mapping"]["status"] == "NOT_FOUND"
    assert finding["current_review_binding"]["review_event_id"]
    for forbidden in ('"person_id"', '"workforce_members"', '"capacity_state"', '"proficiency"'):
        assert forbidden not in response.text
    assert repo.database_path.read_bytes() == before


@pytest.mark.parametrize("query,code", [
    ({"analysis_id": "absent", "snapshot_hash": snapshot().content_hash}, "INVALID_MAPPING_SCOPE"),
    ({"analysis_id": "analysis-1", "snapshot_hash": "sha256:" + "0" * 64}, "SNAPSHOT_NOT_FOUND"),
])
def test_unknown_scope_and_snapshot_are_safe(enabled, query, code):
    client, _ = enabled
    assert_error(client.get(OVERVIEW, params=query), 404, code)


def test_manual_draft_approval_history_and_preserved_judgment(enabled):
    client, repo = enabled
    from tendercite.domain.models import AssessmentRequest

    repo.set_assessment(
        "finding-1", AssessmentRequest(status="PARTIAL", rationale="Synthetic human judgment")
    )
    before_matrix = repo.list_matrix("analysis-1")
    before_reviews = repo.list_reviews("finding-1")
    before_findings = repo.list_findings("analysis-1")
    response = create(client)
    assert response.status_code == 201
    record = response.json()
    assert record["status"] == "DRAFT"
    assert record["revision"] == 1
    assert record["skill_ids"] == [str(SKILL_B)]
    assert record["review_binding"]["review_event_id"] == before_reviews[-1].id
    assert record["approved_at"] is None
    overview = get_overview(client).json()
    assert overview["findings"][0]["mapping"]["status"] == "DRAFT"
    result = approve(client, record)
    assert result.status_code == 200
    approved = result.json()
    assert approved["status"] == "APPROVED"
    assert approved["revision"] == 2
    assert approved["approved_at"] is not None
    assert get_overview(client).json()["findings"][0]["mapping"]["status"] == "APPROVED"
    history = repo.capability_mapping_history(UUID(record["mapping_id"]))
    assert [event.status.value for event in history] == ["DRAFT", "APPROVED"]
    assert repo.list_matrix("analysis-1") == before_matrix
    assert repo.list_reviews("finding-1") == before_reviews
    assert repo.list_findings("analysis-1") == before_findings
    for forbidden in ('"person_id"', '"workforce_members"', '"capacity_state"'):
        assert forbidden not in response.text and forbidden not in result.text


def test_duplicate_draft_revision_conflicts_and_approval_never_retried(enabled):
    client, repo = enabled
    record = create(client).json()
    assert_error(create(client), 409, "MAPPING_ALREADY_EXISTS")
    assert_error(approve(client, record, expected_revision=12), 409, "MAPPING_REVISION_CONFLICT")
    approved = approve(client, record)
    assert approved.status_code == 200
    assert_error(approve(client, record), 409, "MAPPING_REVISION_CONFLICT")
    assert_error(approve(client, approved.json()), 409, "INVALID_MAPPING_TRANSITION")
    assert len(repo.capability_mapping_history(UUID(record["mapping_id"]))) == 2


def test_approval_rejects_changed_review_and_stale_state(enabled):
    client, repo = enabled
    draft = create(client).json()
    repo.add_review("finding-1", ReviewRequest(action="CONFIRM"))
    assert_error(approve(client, draft), 409, "REQUIREMENT_REVIEW_CONFLICT")
    assert get_overview(client).json()["findings"][0]["mapping"]["status"] == "DRAFT"
    assert len(repo.capability_mapping_history(UUID(draft["mapping_id"]))) == 1


@pytest.mark.parametrize("state", ["UNREVIEWED", "REJECTED", "INVALID_GROUNDING"])
def test_ineligible_requirements_cannot_create_draft(client, tmp_path, monkeypatch, state):
    value = finding(review_status=ReviewStatus.UNREVIEWED)
    if state == "INVALID_GROUNDING":
        value.evidence[0].grounding_status = GroundingStatus.INVALID_QUOTE
    repo = seeded_repository(
        tmp_path / ("state-" + state + ".db"),
        confirm=state != "UNREVIEWED",
        findings=[value],
    )
    if state == "REJECTED":
        repo.add_review("finding-1", ReviewRequest(action="REJECT"))
    app.dependency_overrides[get_repository] = lambda: repo
    monkeypatch.setattr(settings, "capability_mapping_api_enabled", True)
    assert get_overview(client).json()["findings"][0]["mapping_preparation_eligible"] is False
    assert_error(create(client), 409, "REQUIREMENT_NOT_ELIGIBLE")
    assert repo.list_capability_mappings() == []


@pytest.mark.parametrize("change,status,code", [
    ({"skill_ids": ["00000000-0000-0000-0000-000000000999"]}, 422, "UNKNOWN_CATALOG_SKILL_UUID"),
    ({"finding_id": "other"}, 404, "INVALID_MAPPING_SCOPE"),
    ({"operator": "ANY_OF"}, 422, "INVALID_MAPPING_INPUT"),
    ({"skill_ids": []}, 422, "INVALID_MAPPING_INPUT"),
    ({"skill_ids": [str(SKILL_B), str(SKILL_B)]}, 422, "INVALID_MAPPING_INPUT"),
    ({"rationale": " "}, 422, "INVALID_MAPPING_INPUT"),
    ({"rationale": "PRIVATE" * 1000}, 422, "INVALID_MAPPING_INPUT"),
    ({"url": "https://PRIVATE.invalid"}, 422, "INVALID_MAPPING_INPUT"),
    ({"database_path": "/PRIVATE"}, 422, "INVALID_MAPPING_INPUT"),
])
def test_invalid_draft_inputs_do_not_write(enabled, change, status, code):
    client, repo = enabled
    assert_error(create(client, draft_body(**change)), status, code)
    assert repo.list_capability_mappings() == []


def test_approval_body_must_match_path_and_require_version_binding(enabled):
    client, _ = enabled
    draft = create(client).json()
    wrong_id = "00000000-0000-0000-0000-000000000999"
    assert_error(
        client.post(
            f"/api/v1/capability-mappings/{wrong_id}/approve",
            json={
                "mapping_id": draft["mapping_id"],
                "expected_revision": 1,
                "expected_review_event_id": draft["review_binding"]["review_event_id"],
                "expected_review_digest": draft["review_binding"]["digest"],
                "rationale": "Explicit approval",
            },
        ),
        422,
        "INVALID_MAPPING_INPUT",
    )
    wrong_digest = "sha256:" + "0" * 64
    assert_error(
        approve(client, draft, expected_review_digest=wrong_digest),
        409,
        "REQUIREMENT_REVIEW_CONFLICT",
    )


@pytest.mark.parametrize("bad,status,code", [
    (b'{"rationale":"PRIVATE","rationale":"PRIVATE"}', 422, "INVALID_MAPPING_INPUT"),
    (b'{"rationale":NaN}', 422, "INVALID_MAPPING_INPUT"),
    (b'{"PRIVATE":', 422, "INVALID_MAPPING_INPUT"),
    (b"\xff", 422, "INVALID_MAPPING_INPUT"),
    (b"[]", 422, "INVALID_MAPPING_INPUT"),
    (b" " * 65537, 413, "MAPPING_REQUEST_TOO_LARGE"),
])
def test_bounded_body_and_ambiguous_json(enabled, bad, status, code):
    client, _ = enabled
    assert_error(
        client.post(DRAFT, content=bad, headers={"Content-Type": "application/json"}),
        status,
        code,
    )


def test_transport_errors_and_query_parameters_are_safe(enabled):
    client, _ = enabled
    assert_error(
        client.post(DRAFT, content=b"PRIVATE", headers={"Content-Type": "text/plain"}),
        415,
        "UNSUPPORTED_MEDIA_TYPE",
    )
    assert_error(
        client.post(
            DRAFT,
            content=json.dumps(draft_body()).encode(),
            headers={"Content-Type": "application/json", "Content-Encoding": "gzip"},
        ),
        415,
        "UNSUPPORTED_CONTENT_ENCODING",
    )
    assert_error(
        client.get(OVERVIEW, params={**params(), "database": "PRIVATE"}),
        422,
        "INVALID_MAPPING_INPUT",
    )


def test_concurrent_draft_creation_creates_single_record(enabled):
    client, repo = enabled
    with ThreadPoolExecutor(max_workers=4) as workers:
        responses = list(workers.map(lambda _: create(client), range(4)))
    assert sum(r.status_code == 201 for r in responses) == 1
    assert sum(r.status_code == 409 for r in responses) == 3
    assert len(repo.list_capability_mappings()) == 1


def test_unexpected_error_is_sanitized_and_creates_no_record(enabled, monkeypatch):
    client, repo = enabled
    monkeypatch.setattr(
        repo, "create_capability_mapping_draft", Mock(side_effect=RuntimeError("PRIVATE"))
    )
    assert_error(create(client), 500, "CAPABILITY_MAPPING_FAILED")


def test_openapi_has_existing_selection_approval_and_overview_contracts():
    document = app.openapi()
    assert "MappingSelection" in str(
        document["paths"][DRAFT]["post"]["requestBody"]
    )
    assert "MappingApprovalInput" in str(
        document["paths"]["/api/v1/capability-mappings/{mapping_id}/approve"]["post"]["requestBody"]
    )
    assert "AssessmentOverview" in str(document["paths"][OVERVIEW]["get"]["responses"]["200"])
