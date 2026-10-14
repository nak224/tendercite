import copy
from uuid import UUID

import pytest
from capability_helpers import example
from matching_helpers import SKILL_A, SKILL_B, analysis, finding, request, snapshot
from pydantic import ValidationError

from tendercite.domain.capability_matching import (
    CapabilityMatchingRequest,
    LimitationCode,
    NonEvaluationReason,
    RecordedSkillCoverage,
)
from tendercite.domain.models import GroundingStatus, ReviewedValue, ReviewStatus
from tendercite.services.capability_matching import CapabilityMatchingError, match_capabilities


def run(selected=None, *, workforce=None, matching_request=None, original_analysis=None):
    workforce = snapshot() if workforce is None else workforce
    selected = [finding()] if selected is None else selected
    return match_capabilities(
        request(workforce) if matching_request is None else matching_request,
        analysis=analysis(*selected) if original_analysis is None else original_analysis,
        findings=selected,
        snapshot=workforce,
    )


def test_single_skill_is_deterministic_preserves_assignments_and_never_verifies_qualification():
    original, workforce = finding(), snapshot()
    saved_finding, saved_snapshot = copy.deepcopy(original), copy.deepcopy(workforce)
    result = run([original], workforce=workforce)
    assert result.model_dump_json() == run([original], workforce=workforce).model_dump_json()
    row = result.findings[0]
    assert row.coverage == RecordedSkillCoverage.ALL
    assert [c.coverage for c in row.candidates] == [
        RecordedSkillCoverage.ALL,
        RecordedSkillCoverage.ALL,
        RecordedSkillCoverage.NONE,
    ]
    assert row.candidates[0].recorded_assignments == workforce.workforce_members[0].skills
    assert row.candidates[0].recorded_assignments[1].proficiency is None
    assert row.candidates[2].recorded_assigned_skill_ids == ()
    assert row.candidates[2].missing_assignment_ids == (SKILL_B,)
    assert row.source_references == tuple(original.evidence)
    assert all(c.qualification == "NOT_VERIFIED" for c in row.candidates)
    assert result.qualification == row.qualification == "NOT_VERIFIED"
    assert original == saved_finding and workforce == saved_snapshot
    # Legacy source models are copied, so manipulating a result cannot edit input evidence.
    row.source_references[0].quote = "Synthetic local edit"
    assert original == saved_finding


def test_all_of_returns_full_partial_and_missing_individual_coverage_in_uuid_order():
    workforce = snapshot()
    result = run(
        workforce=workforce, matching_request=request(workforce, skills=(SKILL_B, SKILL_A))
    )
    row = result.findings[0]
    assert [c.coverage for c in row.candidates] == [
        RecordedSkillCoverage.ALL,
        RecordedSkillCoverage.SOME,
        RecordedSkillCoverage.NONE,
    ]
    assert row.candidates[1].matched_skill_ids == (SKILL_B,)
    assert row.candidates[1].missing_assignment_ids == (SKILL_A,)
    assert all(c.required_skill_ids == (SKILL_A, SKILL_B) for c in row.candidates)
    assert row.mapping.skill_ids == (
        SKILL_B,
        SKILL_A,
    )  # Original operator ordering remains auditable.
    assert [c.person_id for c in row.candidates] == [UUID(int=i) for i in (1, 2, 3)]


def test_skills_on_different_people_are_never_combined_into_all_of_coverage():
    body = example()
    body["workforce_members"][0]["skills"].pop()
    workforce = snapshot(body)
    result = run(
        workforce=workforce, matching_request=request(workforce, skills=(SKILL_A, SKILL_B))
    )
    assert result.findings[0].coverage == RecordedSkillCoverage.SOME
    assert all(c.coverage != RecordedSkillCoverage.ALL for c in result.findings[0].candidates)


def test_empty_workforce_is_visible_when_unmapped_and_unknown_mapping_is_rejected():
    body = example()
    body["workforce_members"], body["skill_catalog"] = [], []
    workforce = snapshot(body)
    result = run(workforce=workforce, matching_request=request(workforce, mappings=()))
    assert result.findings[0].coverage == RecordedSkillCoverage.NOT_EVALUATED
    assert result.findings[0].non_evaluation_reasons == (NonEvaluationReason.UNMAPPED,)
    with pytest.raises(CapabilityMatchingError, match="UNKNOWN_CATALOG_SKILL_UUID"):
        run(workforce=workforce)


@pytest.mark.parametrize(
    "status,reason",
    [
        (ReviewStatus.UNREVIEWED, NonEvaluationReason.UNREVIEWED),
        (ReviewStatus.REJECTED, NonEvaluationReason.REJECTED),
    ],
)
def test_ineligible_review_statuses_remain_visible_without_candidates(status, reason):
    result = run([finding(review_status=status)])
    row = result.findings[0]
    assert row.review_status == status and row.coverage == RecordedSkillCoverage.NOT_EVALUATED
    assert reason in row.non_evaluation_reasons and row.candidates == ()


@pytest.mark.parametrize(
    "grounding,reason",
    [
        (GroundingStatus.INVALID_QUOTE, NonEvaluationReason.INVALID_QUOTE),
        (GroundingStatus.MISSING_EVIDENCE, NonEvaluationReason.MISSING_EVIDENCE),
    ],
)
def test_invalid_grounding_prevents_skill_comparison(grounding, reason):
    selected = finding()
    selected.evidence[0].grounding_status = grounding
    row = run([selected]).findings[0]
    assert row.grounding_status == grounding and row.candidates == ()
    assert row.non_evaluation_reasons == (reason,)


def test_multiple_non_evaluation_reasons_and_missing_evidence_are_preserved():
    workforce = snapshot()
    selected = finding(review_status=ReviewStatus.UNREVIEWED, evidence=[])
    row = run(
        [selected], workforce=workforce, matching_request=request(workforce, mappings=())
    ).findings[0]
    assert row.non_evaluation_reasons == (
        NonEvaluationReason.UNREVIEWED,
        NonEvaluationReason.MISSING_EVIDENCE,
        NonEvaluationReason.UNMAPPED,
    )
    assert row.qualification == "NOT_VERIFIED"


@pytest.mark.parametrize("status", [ReviewStatus.MODIFIED, ReviewStatus.CONFIRMED])
def test_changed_reviewed_meaning_is_not_source_verified_even_when_later_confirmed(status):
    modified = finding(
        review_status=status,
        reviewed_value=ReviewedValue(
            statement="At least three years of PostgreSQL experience",
            category="MUST",
            requirement_type="TECHNICAL",
        ),
    )
    row = run([modified]).findings[0]
    assert row.review_status == status
    assert row.original_value.statement == modified.statement
    assert row.reviewed_value == modified.reviewed_value
    assert row.source_references[0].quote == "Experience with PostgreSQL"
    assert row.grounding_status == GroundingStatus.VERIFIED_QUOTE
    assert row.reviewed_meaning_verification == "NOT_ASSESSED"
    assert LimitationCode.REVIEWED_MEANING_UNVERIFIED in {item.code for item in row.limitations}


@pytest.mark.parametrize(
    "statement,kind",
    [
        ("At least three years of PostgreSQL development experience", "TECHNICAL"),
        ("Certified Kubernetes Administrator", "CERTIFICATE"),
        ("Three available developers for 30 hours per week", "FINANCIAL"),
    ],
)
def test_duration_certification_and_capacity_are_not_inferred_from_assignments(statement, kind):
    body = example()
    body["workforce_members"][0]["skills"][1]["proficiency"] = 5
    workforce = snapshot(body)
    row = run([finding(statement=statement, requirement_type=kind)], workforce=workforce).findings[
        0
    ]
    assert row.coverage == RecordedSkillCoverage.ALL and row.qualification == "NOT_VERIFIED"
    assert row.capacity_evaluation == "NOT_EVALUATED"
    assert {item.code for item in row.limitations} >= {
        LimitationCode.SKILLS_UNVERIFIED,
        LimitationCode.PROFICIENCY_NOT_DURATION,
        LimitationCode.CERTIFICATIONS_UNVERIFIED,
        LimitationCode.CAPACITY_NOT_EVALUATED,
    }
    assert "confidence" not in type(row).model_fields and "score" not in type(row).model_fields


def test_catalog_labels_do_not_influence_uuid_membership():
    body = example()
    body["skill_catalog"][0]["name"] = body["skill_catalog"][1]["name"]
    workforce = snapshot(body)
    row = run(workforce=workforce, matching_request=request(workforce, skills=(SKILL_A,))).findings[
        0
    ]
    assert row.candidates[1].coverage == RecordedSkillCoverage.NONE


def test_finding_output_order_is_stable_while_original_request_is_preserved():
    workforce = snapshot()
    selected = [finding("finding-z"), finding("finding-a")]
    req = request(workforce, finding_ids=("finding-z", "finding-a"))
    first = run(selected, workforce=workforce, matching_request=req)
    second = run(list(reversed(selected)), workforce=workforce, matching_request=req)
    assert first.model_dump_json() == second.model_dump_json()
    assert [f.finding_id for f in first.findings] == ["finding-a", "finding-z"]
    assert first.request == req and first.snapshot_hash == workforce.content_hash
    assert first.assessment_period == workforce.assessment_period
    assert first.snapshot_source_limitations == workforce.source_limitations


@pytest.mark.parametrize(
    "change",
    [
        "duplicate_selection",
        "duplicate_mapping",
        "duplicate_skill",
        "unselected_mapping",
        "unknown_operator",
        "empty_skills",
        "unsupported_version",
        "proficiency_threshold",
        "skill_name",
    ],
)
def test_ambiguous_or_unsupported_requests_fail_before_matching(change):
    body = request(snapshot()).model_dump(mode="json")
    if change == "duplicate_selection":
        body["finding_ids"] *= 2
    elif change == "duplicate_mapping":
        body["mappings"] *= 2
    elif change == "duplicate_skill":
        body["mappings"][0]["skill_ids"] *= 2
    elif change == "unselected_mapping":
        body["mappings"][0]["finding_id"] = "other-finding"
    elif change == "unknown_operator":
        body["mappings"][0]["operator"] = "ANY_OF"
    elif change == "empty_skills":
        body["mappings"][0]["skill_ids"] = []
    elif change == "unsupported_version":
        body["schema_version"] = "capability-matching-request-2"
    elif change == "proficiency_threshold":
        body["mappings"][0]["minimum_proficiency"] = 5
    else:
        body["mappings"][0]["skill_ids"] = ["PostgreSQL"]
    with pytest.raises(ValidationError):
        CapabilityMatchingRequest.model_validate(body)


def test_pure_service_rejects_foreign_missing_and_inconsistent_finding_states():
    for selected, original, error in [
        ([finding(analysis_id="other-analysis")], analysis(finding()), "FINDING_ANALYSIS_MISMATCH"),
        ([], analysis(finding()), "FINDING_NOT_FOUND"),
        (
            [finding(review_status=ReviewStatus.MODIFIED)],
            analysis(finding()),
            "INCONSISTENT_REVIEW_STATE",
        ),
    ]:
        with pytest.raises(CapabilityMatchingError, match=error):
            run(selected, original_analysis=original)


def test_unknown_uuid_is_rejected_even_if_an_existing_catalog_label_is_similar():
    workforce = snapshot()
    with pytest.raises(CapabilityMatchingError, match="UNKNOWN_CATALOG_SKILL_UUID"):
        run(workforce=workforce, matching_request=request(workforce, skills=(UUID(int=999),)))


def test_copied_invalid_requests_are_revalidated_and_unknown_review_states_are_blocked():
    workforce = snapshot()
    invalid = request(workforce).model_copy(update={"finding_ids": ("finding-1", "finding-1")})
    with pytest.raises(CapabilityMatchingError, match="INVALID_MATCHING_REQUEST"):
        run(workforce=workforce, matching_request=invalid)
    unknown_review = finding().model_copy(update={"review_status": "unsupported-review-state"})
    with pytest.raises(CapabilityMatchingError, match="INCONSISTENT_REVIEW_STATE"):
        run([unknown_review])


def test_loaded_duplicates_and_findings_outside_selected_analysis_are_rejected():
    selected = finding()
    for values, original in [
        ([selected, selected], analysis(selected)),
        ([selected], analysis(finding("other-id"))),
    ]:
        with pytest.raises(CapabilityMatchingError):
            run(values, original_analysis=original)


def test_full_assignment_coverage_cannot_be_serialized_as_a_fulfilled_qualification():
    row = run().findings[0]
    with pytest.raises(ValidationError):
        type(row).model_validate(row.model_dump() | {"qualification": "FULFILLED"})


@pytest.mark.parametrize("capacity_change", ["zero", "negative", "positive_daily_conflict"])
def test_capacity_values_do_not_change_assignment_coverage_or_produce_availability(capacity_change):
    body = example()
    cap = body["workforce_members"][0]["capacity"]
    if capacity_change == "zero":
        for key in cap:
            if key.endswith("hours"):
                cap[key] = "0.00"
    elif capacity_change == "negative":
        cap["remaining_hours"] = "-9.00"
    workforce = snapshot(body)
    result = run(workforce=workforce)
    assert [c.coverage for c in result.findings[0].candidates] == [
        c.coverage for c in run().findings[0].candidates
    ]
    assert result.snapshot_hash == workforce.content_hash
    assert result.capacity_evaluation == "NOT_EVALUATED"
    assert all(c.capacity_evaluation == "NOT_EVALUATED" for c in result.findings[0].candidates)
