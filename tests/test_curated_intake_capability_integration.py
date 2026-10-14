import socket

import httpx
import pytest
from curated_intake_helpers import intake, repository, upload
from matching_helpers import SKILL_B, snapshot

from tendercite.domain.capability_mapping_records import (
    CapabilityMappingError,
    MappingApprovalInput,
)
from tendercite.domain.capability_workflow import AssessmentSelection, MappingSelection
from tendercite.domain.models import AssessmentRequest, ReviewedValue
from tendercite.domain.review import ReviewRequest
from tendercite.services.capability_workflow import (
    assessment_overview,
    assessment_report,
    prepare_mapping_draft,
)


@pytest.mark.parametrize("later_review", ["CONFIRM", "MODIFY", "REJECT"])
def test_real_pdf_intake_review_mapping_matching_and_invalidation(
    client, monkeypatch, later_review
):
    def forbidden(*_args, **_kwargs):
        pytest.fail("Synthetic intake workflow attempted external communication")

    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", forbidden)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", forbidden)
    document = upload(client)
    repo = repository(client)
    workforce = snapshot()
    repo.import_capability_snapshot(workforce)
    before_matrix = repo.list_matrix()
    result = repo.commit_curated_intake(intake(document))
    run = repo.get_analysis(result.analysis_id)
    finding_id = run.findings[0].id
    assert repo.list_matrix() == before_matrix
    scope = dict(analysis_id=run.id, snapshot_hash=workforce.content_hash)
    selection = MappingSelection(
        **scope,
        finding_id=finding_id,
        operator="ALL_OF",
        skill_ids=(SKILL_B,),
        rationale="Explicit synthetic PostgreSQL association",
    )
    assessment = AssessmentSelection(**scope, finding_ids=(finding_id,))
    overview = assessment_overview(**scope, repository=repo)
    assert not overview.findings[0].mapping_preparation_eligible
    with pytest.raises(CapabilityMappingError, match="REQUIREMENT_NOT_ELIGIBLE"):
        prepare_mapping_draft(selection, repository=repo)
    assert assessment_report(assessment, repository=repo).findings[0].coverage == "NOT_EVALUATED"
    # Simulated explicit production review; occurrence verification did not create it.
    response = client.post(
        f"/api/v1/findings/{finding_id}/reviews",
        json={"action": "CONFIRM", "comment": "Self-authored simulation only"},
    )
    assert response.status_code == 201
    assert len(repo.list_reviews(finding_id)) == 1
    draft = repo.create_capability_mapping_draft(prepare_mapping_draft(selection, repository=repo))
    assert assessment_report(assessment, repository=repo).summary.drafts == 1
    approved = repo.approve_capability_mapping(
        MappingApprovalInput(
            mapping_id=draft.mapping_id,
            expected_revision=draft.revision,
            expected_review_event_id=draft.review_binding.review_event_id,
            expected_review_digest=draft.review_binding.digest,
            rationale="Explicit simulated approval of association, not qualification",
        )
    )
    repo.set_assessment(
        finding_id,
        AssessmentRequest(
            status="CLARIFICATION_NEEDED", rationale="Separate synthetic operator decision"
        ),
    )
    before = repo.database_path.read_bytes()
    matrix = repo.list_matrix()
    report = assessment_report(assessment, repository=repo)
    assert report.summary.current_approved == 1 and report.summary.not_eligible_for_matching == 0
    assert report.findings[0].individual_counts.model_dump() == dict(
        all_required_recorded=2, some_required_recorded=0, no_required_recorded=1
    )
    assert report.findings[0].qualification == "NOT_VERIFIED"
    assert report.findings[0].capacity_evaluation == "NOT_EVALUATED"
    assert repo.database_path.read_bytes() == before and repo.list_matrix() == matrix
    # Identical intake retry after review/mapping/assessment must preserve all histories.
    assert repo.commit_curated_intake(intake(document)).status == "ALREADY_PRESENT"
    assert repo.database_path.read_bytes() == before
    repo.add_review(
        finding_id,
        ReviewRequest(
            action=later_review,
            reviewed_value=ReviewedValue(
                statement="Five years of PostgreSQL experience",
                category="MUST",
                requirement_type="TECHNICAL",
            )
            if later_review == "MODIFY"
            else None,
        ),
    )
    stale = assessment_report(assessment, repository=repo)
    assert stale.summary.stale == 1 and stale.summary.current_approved == 0
    assert stale.findings[0].coverage == "NOT_EVALUATED"
    assert "PERSISTED_MAPPING_STALE" in stale.findings[0].non_evaluation_reasons
    assert repo.capability_mapping_history(approved.mapping_id)[-1].status == "APPROVED"


def test_curated_private_directory_supports_existing_capability_handoff():
    from pathlib import Path

    from tendercite.cli.capability_workflow import private_workflow_path

    root = Path(__file__).absolute().parents[1]
    path = root / "data/curated/demo/assessment.json"
    assert private_workflow_path(path) == path
