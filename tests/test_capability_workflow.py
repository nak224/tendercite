"""Synthetic workflow regressions; no genuine review, qualification or capacity claims."""

import json
import socket
from contextlib import contextmanager
from uuid import UUID

import httpx
import pytest
from capability_helpers import example
from matching_helpers import SKILL_A, SKILL_B, analysis, finding, snapshot
from persistent_mapping_helpers import (
    approval_input,
    approved_mapping,
    draft_input,
    revision_input,
    revocation_input,
    seeded_repository,
)
from pydantic import ValidationError

from tendercite.domain.capability_mapping_records import CapabilityMappingError, MappingDraftInput
from tendercite.domain.capability_matching import RecordedSkillCoverage
from tendercite.domain.capability_workflow import AssessmentSelection, MappingSelection
from tendercite.domain.models import GroundingStatus, RequirementType, ReviewedValue, ReviewStatus
from tendercite.domain.review import ReviewRequest
from tendercite.repositories.sqlite import SqliteRepository
from tendercite.services.capability_workflow import (
    assessment_overview,
    assessment_report,
    prepare_mapping_draft,
)


def selection(**changes):
    return MappingSelection(
        **(
            dict(
                analysis_id="analysis-1",
                finding_id="finding-1",
                snapshot_hash=snapshot().content_hash,
                operator="ALL_OF",
                skill_ids=(SKILL_B,),
                rationale="Operator explicitly selects synthetic PostgreSQL skill",
                operator_alias="synthetic_operator",
            )
            | changes
        )
    )


def assessment(**changes):
    return AssessmentSelection(
        **(
            dict(
                analysis_id="analysis-1",
                snapshot_hash=snapshot().content_hash,
                finding_ids=("finding-1",),
            )
            | changes
        )
    )


@pytest.fixture
def repo(tmp_path):
    return seeded_repository(tmp_path / "workflow.db")


@pytest.mark.parametrize(
    "changes",
    [
        {"skill_ids": ()},
        {"skill_ids": (SKILL_B, SKILL_B)},
        {"operator": "ANY_OF"},
        {"schema_version": "future"},
        {"operator_alias": "synthetic@example.invalid"},
        {"rationale": " "},
        {"rationale": "x" * 2001},
        {"finding_id": ""},
        {"extra": "unsupported"},
    ],
)
def test_strict_selection_contract(changes):
    with pytest.raises(ValidationError):
        selection(**changes)


def test_canonical_selection_and_json_roundtrip():
    value = selection(skill_ids=(SKILL_B, SKILL_A))
    assert value.skill_ids == (SKILL_A, SKILL_B)
    assert MappingSelection.model_validate_json(value.model_dump_json()) == value
    assert assessment(finding_ids=("z", "a")).finding_ids == ("a", "z")
    with pytest.raises(ValidationError):
        assessment(finding_ids=("a", "a"))
    with pytest.raises(ValidationError):
        assessment(finding_ids=tuple(str(i) for i in range(101)))


def test_overview_keeps_provenance_but_excludes_personnel(repo):
    value = assessment_overview(
        **assessment().model_dump(exclude={"schema_version", "finding_ids"}), repository=repo
    )
    assert value.analysis_finding_ids == ("finding-1",)
    assert value.snapshot.content_hash == snapshot().content_hash
    assert value.snapshot.assessment_period == snapshot().assessment_period
    assert value.snapshot.skill_catalog == snapshot().skill_catalog
    assert value.snapshot.workforce_member_count == 3
    row = value.findings[0]
    assert row.mapping.status == "NOT_FOUND"
    assert row.mapping_preparation_eligible
    assert row.source_references == tuple(repo.get_finding("finding-1").evidence)
    body = value.model_dump_json()
    for forbidden in ("person_id", "workforce_members", "capacity_state", "proficiency", "email"):
        assert f'"{forbidden}":' not in body


def test_preparation_is_read_only_and_produces_existing_contract(repo):
    before = repo.database_path.read_bytes()
    request = prepare_mapping_draft(selection(), repository=repo)
    assert MappingDraftInput.model_validate_json(request.model_dump_json()) == request
    assert request.expected_review_event_id == repo.list_reviews("finding-1")[-1].id
    assert request.skill_ids == (SKILL_B,)
    assert repo.database_path.read_bytes() == before
    assert repo.list_capability_mappings() == []
    record = repo.create_capability_mapping_draft(request)
    assert record.status == "DRAFT"
    report = assessment_report(assessment(), repository=repo)
    assert report.summary.drafts == 1 and report.summary.not_eligible_for_matching == 1
    assert report.findings[0].individual_coverage == ()
    assert "PERSISTED_MAPPING_NOT_APPROVED" in report.findings[0].non_evaluation_reasons


@pytest.mark.parametrize("action", ["CONFIRM", "MODIFY", "REJECT"])
def test_prepared_input_rejects_changed_review_before_create(repo, action):
    prepared = prepare_mapping_draft(selection(), repository=repo)
    repo.add_review(
        "finding-1",
        ReviewRequest(
            action=action,
            reviewed_value=ReviewedValue(
                statement="Changed PostgreSQL requirement",
                category="MUST",
                requirement_type="TECHNICAL",
            )
            if action == "MODIFY"
            else None,
        ),
    )
    with pytest.raises(CapabilityMappingError, match="REQUIREMENT_REVIEW_CONFLICT"):
        repo.create_capability_mapping_draft(prepared)
    assert repo.list_capability_mappings() == []


@pytest.mark.parametrize("state", ["UNREVIEWED", "REJECTED", "INVALID_QUOTE", "MISSING_EVIDENCE"])
def test_ineligible_findings_remain_visible_but_cannot_prepare(tmp_path, state):
    value = finding(review_status=ReviewStatus.UNREVIEWED)
    if state == "INVALID_QUOTE":
        value.evidence[0].grounding_status = GroundingStatus.INVALID_QUOTE
    elif state == "MISSING_EVIDENCE":
        value.evidence = []
    repo = seeded_repository(tmp_path / "state.db", confirm=state != "UNREVIEWED", findings=[value])
    if state == "REJECTED":
        repo.add_review(value.id, ReviewRequest(action="REJECT"))
    overview = assessment_overview(
        analysis_id="analysis-1", snapshot_hash=snapshot().content_hash, repository=repo
    )
    assert not overview.findings[0].mapping_preparation_eligible
    with pytest.raises(CapabilityMappingError, match="REQUIREMENT_NOT_ELIGIBLE"):
        prepare_mapping_draft(selection(), repository=repo)
    report = assessment_report(assessment(), repository=repo)
    assert report.findings[0].coverage == "NOT_EVALUATED"
    assert report.summary.without_mapping == 1
    assert report.summary.not_eligible_for_matching == 1


@pytest.mark.parametrize(
    "changes, error",
    [
        ({"analysis_id": "missing"}, "INVALID_MAPPING_SCOPE"),
        ({"snapshot_hash": "sha256:" + "0" * 64}, "SNAPSHOT_NOT_FOUND"),
        ({"finding_id": "missing"}, "INVALID_MAPPING_SCOPE"),
        ({"skill_ids": (UUID(int=999),)}, "UNKNOWN_CATALOG_SKILL_UUID"),
    ],
)
def test_scope_and_catalog_checks(repo, changes, error):
    with pytest.raises(CapabilityMappingError, match=error):
        prepare_mapping_draft(selection(**changes), repository=repo)


def test_foreign_analysis_finding_is_never_substituted(repo):
    other = finding("foreign", "analysis-2", review_status=ReviewStatus.UNREVIEWED)
    repo.save_analysis(analysis(other, analysis_id="analysis-2"))
    repo.add_review("foreign", ReviewRequest(action="CONFIRM"))
    with pytest.raises(CapabilityMappingError, match="INVALID_MAPPING_SCOPE"):
        assessment_report(assessment(finding_ids=("foreign",)), repository=repo)


def test_modified_requirement_preserves_original_and_binding(repo):
    original = repo.get_finding("finding-1").statement
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
    prepared = prepare_mapping_draft(selection(), repository=repo)
    record = repo.create_capability_mapping_draft(prepared)
    repo.approve_capability_mapping(approval_input(record))
    report = assessment_report(assessment(), repository=repo)
    row = report.findings[0]
    assert row.finding.original_requirement.statement == original
    assert row.finding.effective_requirement.statement == "Five years of PostgreSQL experience"
    assert row.finding.review_status == "MODIFIED"
    assert row.qualification == "NOT_VERIFIED"
    assert row.capacity_evaluation == "NOT_EVALUATED"
    assert row.reviewed_meaning_verification == "NOT_ASSESSED"
    assert "CHANGED_REVIEWED_MEANING_NOT_SOURCE_VERIFIED" in {
        limitation.code for limitation in row.limitations
    }


@pytest.mark.parametrize(
    "skills, counts", [((SKILL_B,), (2, 0, 1)), ((SKILL_A, SKILL_B), (1, 1, 1))]
)
def test_report_counts_existing_individual_coverage_and_preserves_state(repo, skills, counts):
    record = repo.create_capability_mapping_draft(draft_input(repo, skill_ids=skills))
    repo.approve_capability_mapping(approval_input(record))
    before = repo.database_path.read_bytes()
    report = assessment_report(
        assessment(), repository=SqliteRepository(repo.database_path, read_only=True)
    )
    assert repo.database_path.read_bytes() == before
    row = report.findings[0]
    assert tuple(row.individual_counts.model_dump().values()) == counts
    assert tuple(c.person_id for c in row.individual_coverage) == tuple(
        UUID(int=i) for i in (1, 2, 3)
    )
    assert row.coverage == RecordedSkillCoverage.ALL
    assert report.summary.current_approved == 1
    assert report.summary.not_eligible_for_matching == 0
    assert report == assessment_report(assessment(), repository=repo)
    assert "capacity_state" not in report.model_dump_json()


@pytest.mark.parametrize("action", ["CONFIRM", "MODIFY", "REJECT"])
def test_stale_approvals_remain_unusable_in_report(repo, action):
    record = approved_mapping(repo)
    repo.add_review(
        "finding-1",
        ReviewRequest(
            action=action,
            reviewed_value=ReviewedValue(
                statement="Changed skill requirement", category="MUST", requirement_type="TECHNICAL"
            )
            if action == "MODIFY"
            else None,
        ),
    )
    report = assessment_report(assessment(), repository=repo)
    assert report.summary.stale == 1 and report.summary.current_approved == 0
    assert report.findings[0].finding.mapping.status == "STALE"
    assert "PERSISTED_MAPPING_STALE" in report.findings[0].non_evaluation_reasons
    assert report.findings[0].coverage == "NOT_EVALUATED"
    assert repo.capability_mapping_history(record.mapping_id)[-1].status == "APPROVED"
    error = "REQUIREMENT_NOT_ELIGIBLE" if action == "REJECT" else "MAPPING_ALREADY_EXISTS"
    with pytest.raises(CapabilityMappingError, match=error):
        prepare_mapping_draft(selection(), repository=repo)
    revised = repo.revise_capability_mapping(revision_input(repo, record))
    assert assessment_report(assessment(), repository=repo).summary.drafts == 1
    if action != "REJECT":
        repo.approve_capability_mapping(approval_input(revised))
        assert assessment_report(assessment(), repository=repo).summary.current_approved == 1


def test_revocation_does_not_fall_back_to_historical_approval(repo):
    record = approved_mapping(repo)
    repo.revoke_capability_mapping(revocation_input(record))
    report = assessment_report(assessment(), repository=repo)
    assert report.summary.revoked == 1
    assert report.findings[0].coverage == "NOT_EVALUATED"
    assert "PERSISTED_MAPPING_REVOKED" in report.findings[0].non_evaluation_reasons


def test_historical_snapshots_never_share_mappings(repo):
    approved_mapping(repo)
    body = example()
    body["assessment_period"] = {"start_date": "2027-03-01", "end_date": "2027-03-07"}
    second = snapshot(body)
    repo.import_capability_snapshot(second)
    report = assessment_report(assessment(snapshot_hash=second.content_hash), repository=repo)
    assert report.snapshot_hash == second.content_hash
    assert report.snapshot.assessment_period == second.assessment_period
    assert report.summary.without_mapping == 1
    assert report.findings[0].individual_coverage == ()


def test_empty_selection_is_no_op_but_still_checks_scope(repo):
    report = assessment_report(assessment(finding_ids=()), repository=repo)
    assert report.findings == () and report.summary.selected_finding_count == 0
    with pytest.raises(CapabilityMappingError, match="SNAPSHOT_NOT_FOUND"):
        assessment_report(
            assessment(finding_ids=(), snapshot_hash="sha256:" + "0" * 64), repository=repo
        )
    with pytest.raises(CapabilityMappingError, match="INVALID_MAPPING_SCOPE"):
        assessment_report(assessment(finding_ids=(), analysis_id="missing"), repository=repo)


def test_ordering_all_mapping_states_and_no_type_inference(tmp_path):
    values = [
        finding(i, requirement_type=RequirementType.DEADLINE) for i in ("z", "d", "b", "a", "c")
    ]
    repo = seeded_repository(tmp_path / "ordered.db", findings=values)
    for name in ("a", "b", "c", "d"):
        record = repo.create_capability_mapping_draft(draft_input(repo, finding_id=name))
        if name != "b":
            record = repo.approve_capability_mapping(approval_input(record))
        if name == "c":
            repo.add_review(name, ReviewRequest(action="CONFIRM"))
        if name == "d":
            repo.revoke_capability_mapping(revocation_input(record))
    report = assessment_report(assessment(finding_ids=("z", "d", "b", "a", "c")), repository=repo)
    assert [r.finding.finding_id for r in report.findings] == ["a", "b", "c", "d", "z"]
    assert report.summary.model_dump() == dict(
        selected_finding_count=5,
        current_approved=1,
        drafts=1,
        stale=1,
        revoked=1,
        without_mapping=1,
        not_eligible_for_matching=4,
    )
    assert all(
        r.finding.effective_requirement.requirement_type == "DEADLINE" for r in report.findings
    )
    assert (
        "REQUIRES_EXPLICIT_OPERATOR_SELECTION"
        in assessment_overview(
            analysis_id="analysis-1", snapshot_hash=snapshot().content_hash, repository=repo
        ).model_dump_json()
    )


def test_report_reuses_one_read_snapshot_and_no_network(repo, monkeypatch):
    approved_mapping(repo)
    statements, connections = [], []
    connect = repo._connect

    @contextmanager
    def traced():
        with connect() as conn:
            connections.append(conn)
            conn.set_trace_callback(statements.append)
            yield conn

    def forbidden(*args, **kwargs):
        pytest.fail("Workflow attempted network communication")

    monkeypatch.setattr(repo, "_connect", traced)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", forbidden)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", forbidden)
    report = assessment_report(assessment(), repository=repo)
    assert report.summary.current_approved == 1
    assert len(connections) == 1
    assert statements.count("BEGIN") == 1
    assert sum("FROM capability_snapshots" in s for s in statements) == 1
    assert not any(s.startswith(("INSERT", "UPDATE", "DELETE", "CREATE")) for s in statements)
    assert json.loads(report.model_dump_json())["qualification"] == "NOT_VERIFIED"
