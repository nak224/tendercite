import json
from uuid import UUID

import pytest
from matching_helpers import SKILL_A, SKILL_B, analysis, finding, snapshot
from persistent_mapping_helpers import approval_input, draft_input, seeded_repository
from pydantic import ValidationError

from tendercite.domain.capability_mapping_records import (
    CapabilityMappingError,
    CapabilityMappingRecord,
    MappingApprovalInput,
    MappingDraftInput,
    RequirementReviewBinding,
    reviewed_value_digest,
)
from tendercite.domain.models import GroundingStatus, ReviewStatus
from tendercite.domain.review import ReviewRequest


@pytest.fixture
def repo(tmp_path):
    return seeded_repository(tmp_path / "synthetic.db")


def test_strict_versioned_contracts_and_canonical_uuid_order(repo):
    request = draft_input(repo, skill_ids=(SKILL_B, SKILL_A))
    assert request.skill_ids == (SKILL_A, SKILL_B)
    parsed = MappingDraftInput.model_validate_json(request.model_dump_json())
    assert parsed == request
    draft = repo.create_capability_mapping_draft(parsed)
    assert CapabilityMappingRecord.model_validate_json(draft.model_dump_json()) == draft
    assert draft.matching_contract_version == "requirement-skill-mapping-1"
    with pytest.raises(ValidationError):
        draft.rationale = "overwrite"


@pytest.mark.parametrize(
    "change",
    [
        {"schema_version": "capability-mapping-draft-2"},
        {"operator": "ANY_OF"},
        {"skill_ids": []},
        {"skill_ids": [str(SKILL_B), str(SKILL_B)]},
        {"skill_ids": ["PostgreSQL"]},
        {"snapshot_hash": "latest"},
        {"analysis_id": 12},
        {"finding_id": ""},
        {"finding_id": "a" * 129},
        {"rationale": " "},
        {"rationale": "r" * 2001},
        {"operator_alias": "person@example.invalid"},
        {"qualification": "VERIFIED"},
        {"minimum_proficiency": 3},
        {"expected_review_digest": "x"},
        {"expected_review_event_id": False},
    ],
)
def test_invalid_contract_payloads_are_rejected_without_input_echo(repo, change):
    body = draft_input(repo).model_dump(mode="json") | change
    with pytest.raises(ValidationError) as error:
        MappingDraftInput.model_validate_json(json.dumps(body))
    assert "input_value=" not in str(error.value)


def test_review_digest_uses_exact_interpretation_and_binding_is_immutable(repo):
    binding = repo.get_mapping_review_binding("analysis-1", "finding-1", snapshot().content_hash)
    body = binding.model_dump(mode="json")
    assert body["digest"] == reviewed_value_digest(binding.value)
    for field, value in (
        ("statement", "Changed PostgreSQL statement"),
        ("category", "RISK"),
        ("requirement_type", "REFERENCE"),
    ):
        altered = json.loads(json.dumps(body))
        altered["value"][field] = value
        with pytest.raises(ValidationError, match="digest mismatch"):
            RequirementReviewBinding.model_validate_json(json.dumps(altered))
    with pytest.raises(ValidationError):
        binding.value.statement = "mutation"


def test_unknown_skills_cross_analysis_and_missing_snapshot_are_not_negative_results(repo):
    good = draft_input(repo)
    other = finding(
        "other-finding", analysis_id="other-analysis", review_status=ReviewStatus.UNREVIEWED
    )
    repo.save_analysis(analysis(other, analysis_id="other-analysis"))
    changes = [
        ({"skill_ids": (UUID(int=999),)}, "UNKNOWN_CATALOG_SKILL_UUID"),
        ({"finding_id": other.id}, "INVALID_MAPPING_SCOPE"),
        ({"analysis_id": "missing-analysis"}, "INVALID_MAPPING_SCOPE"),
        ({"finding_id": "missing-finding"}, "INVALID_MAPPING_SCOPE"),
        ({"snapshot_hash": "sha256:" + "0" * 64}, "SNAPSHOT_NOT_FOUND"),
        ({"expected_review_event_id": "wrong-event"}, "REQUIREMENT_REVIEW_CONFLICT"),
        ({"expected_review_digest": "sha256:" + "0" * 64}, "REQUIREMENT_REVIEW_CONFLICT"),
    ]
    for change, code in changes:
        request = MappingDraftInput.model_validate(good.model_dump() | change)
        with pytest.raises(CapabilityMappingError, match=code):
            repo.create_capability_mapping_draft(request)
    assert repo.list_capability_mappings() == []


def test_programmatic_model_copy_cannot_bypass_request_checks(repo):
    request = draft_input(repo).model_copy(update={"skill_ids": (SKILL_B, SKILL_B)})
    with pytest.raises(CapabilityMappingError, match="INVALID_MAPPING_INPUT"):
        repo.create_capability_mapping_draft(request)
    draft = repo.create_capability_mapping_draft(draft_input(repo))
    request = approval_input(draft).model_copy(update={"expected_revision": True})
    with pytest.raises(CapabilityMappingError, match="INVALID_MAPPING_INPUT"):
        repo.approve_capability_mapping(request)
    body = approval_input(draft).model_dump(mode="json")
    for change in (
        {"expected_revision": "1"},
        {"expected_revision": 0},
        {"expected_revision": True},
    ):
        with pytest.raises(ValidationError):
            MappingApprovalInput.model_validate_json(json.dumps(body | change))


@pytest.mark.parametrize(
    "status", [GroundingStatus.MISSING_EVIDENCE, GroundingStatus.INVALID_QUOTE]
)
def test_invalid_grounding_prevents_approval_without_changing_history(tmp_path, status):
    value = finding(review_status=ReviewStatus.UNREVIEWED)
    value.evidence[0].grounding_status = status
    repo = seeded_repository(tmp_path / "bad-grounding.db", findings=[value])
    draft = repo.create_capability_mapping_draft(draft_input(repo))
    with pytest.raises(CapabilityMappingError, match="REQUIREMENT_NOT_ELIGIBLE"):
        repo.approve_capability_mapping(approval_input(draft))
    assert repo.capability_mapping_history(draft.mapping_id) == [draft]


def test_drafts_can_exist_for_unreviewed_or_rejected_findings_but_are_not_approved(tmp_path):
    repo = seeded_repository(tmp_path / "unreviewed.db", confirm=False)
    draft = repo.create_capability_mapping_draft(draft_input(repo))
    assert draft.review_binding.review_event_id is None
    with pytest.raises(ValidationError):
        approval_input(draft)
    repo.add_review("finding-1", ReviewRequest(action="REJECT"))
    from persistent_mapping_helpers import revision_input

    revised = repo.revise_capability_mapping(revision_input(repo, draft))
    with pytest.raises(CapabilityMappingError, match="REQUIREMENT_NOT_ELIGIBLE"):
        repo.approve_capability_mapping(approval_input(revised))


def test_record_versions_and_approval_metadata_cannot_be_forged(repo):
    draft = repo.create_capability_mapping_draft(draft_input(repo))
    body = draft.model_dump(mode="json")
    for change in (
        {"schema_version": "future"},
        {"matching_contract_version": "future"},
        {"status": "STALE"},
        {"status": "APPROVED"},
        {"approved_at": body["recorded_at"]},
        {"created_at": "2027-01-01T00:00:00"},
        {"person_id": "should-not-be-stored"},
    ):
        with pytest.raises(ValidationError):
            CapabilityMappingRecord.model_validate_json(json.dumps(body | change))
