"""Private assessment-support contracts, independent of qualification or decisions."""

from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import Field, model_validator

from tendercite.domain.capabilities_v1 import AssessmentPeriod, CatalogSkill
from tendercite.domain.capability_mapping_records import (
    LocalId,
    MappingModel,
    MappingStatus,
    MappingUnavailableReason,
    OperatorAlias,
    Rationale,
    RequirementInterpretation,
    RequirementReviewBinding,
    SkillSelection,
)
from tendercite.domain.capability_matching import (
    EvidenceLimitation,
    NonEvaluationReason,
    RecordedSkillCoverage,
    SnapshotHash,
)
from tendercite.domain.models import EvidenceRef, GroundingStatus, ReviewStatus


class WorkflowScope(MappingModel):
    analysis_id: LocalId
    snapshot_hash: SnapshotHash


class MappingSelection(SkillSelection, WorkflowScope):
    schema_version: Literal["capability-workflow-mapping-selection-1"] = (
        "capability-workflow-mapping-selection-1"
    )
    finding_id: LocalId
    rationale: Rationale
    operator_alias: OperatorAlias | None = None


class AssessmentSelection(WorkflowScope):
    schema_version: Literal["capability-workflow-assessment-selection-1"] = (
        "capability-workflow-assessment-selection-1"
    )
    # Empty selections are explicit no-op assessments, never implicit "select all".
    finding_ids: Annotated[tuple[LocalId, ...], Field(max_length=100)]

    @model_validator(mode="after")
    def unique_findings(self) -> Self:
        if len(set(self.finding_ids)) != len(self.finding_ids):
            raise ValueError("Duplicate finding IDs")
        object.__setattr__(self, "finding_ids", tuple(sorted(self.finding_ids)))
        return self


class SnapshotOverview(MappingModel):
    content_hash: SnapshotHash
    schema_version: Literal["1.0"] = "1.0"
    source_system: Literal["team-capacity-engine"] = "team-capacity-engine"
    assessment_period: AssessmentPeriod
    skill_catalog: tuple[CatalogSkill, ...]
    workforce_member_count: Annotated[int, Field(ge=0)]
    source_limitations: tuple[str, ...]


class MappingOverview(MappingModel):
    status: MappingStatus
    mapping_id: UUID | None
    revision: Annotated[int, Field(ge=1)] | None
    operator: Literal["ALL_OF"] | None
    skill_ids: tuple[UUID, ...]
    unavailable_reasons: tuple[MappingUnavailableReason, ...]
    approved_review_binding: RequirementReviewBinding | None


class FindingOverview(MappingModel):
    finding_id: LocalId
    original_requirement: RequirementInterpretation
    effective_requirement: RequirementInterpretation
    review_status: ReviewStatus
    grounding_status: GroundingStatus
    source_references: tuple[EvidenceRef, ...]
    current_review_binding: RequirementReviewBinding
    # Eligibility is a review/source gate, not an inference of skill relevance.
    mapping_preparation_eligible: bool
    mapping: MappingOverview


class AssessmentOverview(WorkflowScope):
    schema_version: Literal["capability-workflow-overview-1"] = "capability-workflow-overview-1"
    analysis_finding_ids: tuple[LocalId, ...]
    snapshot: SnapshotOverview
    findings: tuple[FindingOverview, ...]
    skill_relevance: Literal["REQUIRES_EXPLICIT_OPERATOR_SELECTION"] = (
        "REQUIRES_EXPLICIT_OPERATOR_SELECTION"
    )


class CoverageCounts(MappingModel):
    all_required_recorded: Annotated[int, Field(ge=0)]
    some_required_recorded: Annotated[int, Field(ge=0)]
    no_required_recorded: Annotated[int, Field(ge=0)]


class IndividualAssignmentCoverage(MappingModel):
    person_id: UUID
    matched_skill_ids: tuple[UUID, ...]
    missing_assignment_ids: tuple[UUID, ...]
    coverage: RecordedSkillCoverage


class AssessedFinding(MappingModel):
    finding: FindingOverview
    coverage: RecordedSkillCoverage
    individual_counts: CoverageCounts
    individual_coverage: tuple[IndividualAssignmentCoverage, ...]
    non_evaluation_reasons: tuple[NonEvaluationReason, ...]
    limitations: tuple[EvidenceLimitation, ...]
    qualification: Literal["NOT_VERIFIED"] = "NOT_VERIFIED"
    capacity_evaluation: Literal["NOT_EVALUATED"] = "NOT_EVALUATED"
    reviewed_meaning_verification: Literal["NOT_ASSESSED"] = "NOT_ASSESSED"


class AssessmentSummary(MappingModel):
    selected_finding_count: Annotated[int, Field(ge=0)]
    current_approved: Annotated[int, Field(ge=0)]
    drafts: Annotated[int, Field(ge=0)]
    stale: Annotated[int, Field(ge=0)]
    revoked: Annotated[int, Field(ge=0)]
    without_mapping: Annotated[int, Field(ge=0)]
    not_eligible_for_matching: Annotated[int, Field(ge=0)]


EVIDENCE_BOUNDARIES = (
    "Source quotation verification establishes occurrence, not interpretation correctness.",
    "Requirement review records the operator's interpretation, not tender fulfillment.",
    "Mapping approval records an operator skill association, not independent evidence.",
    "Workforce skill assignments are internal records; proficiency is not experience years.",
    "Independent qualification, certificates and bidder eligibility are not established.",
    "Capacity, commitment, team feasibility and Go/No-Go are not evaluated.",
)


class AssessmentReport(WorkflowScope):
    schema_version: Literal["capability-workflow-report-1"] = "capability-workflow-report-1"
    matching_contract_version: Literal["capability-matching-result-1"] = (
        "capability-matching-result-1"
    )
    algorithm_version: Literal["explicit-uuid-membership-1"] = "explicit-uuid-membership-1"
    purpose: Literal["ASSESSMENT_SUPPORT_ONLY"] = "ASSESSMENT_SUPPORT_ONLY"
    snapshot: SnapshotOverview
    summary: AssessmentSummary
    findings: tuple[AssessedFinding, ...]
    evidence_boundaries: tuple[str, ...] = EVIDENCE_BOUNDARIES
    qualification: Literal["NOT_VERIFIED"] = "NOT_VERIFIED"
    capacity_evaluation: Literal["NOT_EVALUATED"] = "NOT_EVALUATED"
