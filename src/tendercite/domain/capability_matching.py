"""Versioned candidate-discovery contracts; no tender qualification or decisions."""

from enum import StrEnum
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from tendercite.domain.capabilities_v1 import AssessmentPeriod, AssignedSkill, EngagementType
from tendercite.domain.models import EvidenceRef, GroundingStatus, ReviewedValue, ReviewStatus

MATCHING_ALGORITHM_VERSION = "explicit-uuid-membership-1"
Identifier = Annotated[str, StringConstraints(strict=True, min_length=1, pattern=r"\S")]
SnapshotHash = Annotated[str, StringConstraints(strict=True, pattern=r"^sha256:[0-9a-f]{64}$")]


class MatchingModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid", frozen=True, hide_input_in_errors=True, revalidate_instances="always"
    )


class RequirementSkillMapping(MatchingModel):
    schema_version: Literal["requirement-skill-mapping-1"] = "requirement-skill-mapping-1"
    finding_id: Identifier
    operator: Literal["ALL_OF"]
    skill_ids: Annotated[tuple[UUID, ...], Field(min_length=1)]

    @model_validator(mode="after")
    def unique_skills(self) -> Self:
        if len(set(self.skill_ids)) != len(self.skill_ids):
            raise ValueError("Duplicate skill UUIDs")
        return self


class CapabilityMatchingRequest(MatchingModel):
    schema_version: Literal["capability-matching-request-1"] = "capability-matching-request-1"
    analysis_id: Identifier
    snapshot_hash: SnapshotHash
    finding_ids: Annotated[tuple[Identifier, ...], Field(min_length=1, max_length=100)]
    mappings: tuple[RequirementSkillMapping, ...]

    @model_validator(mode="after")
    def unambiguous_selection(self) -> Self:
        if len(set(self.finding_ids)) != len(self.finding_ids):
            raise ValueError("Duplicate selected findings")
        mapped = tuple(m.finding_id for m in self.mappings)
        if len(set(mapped)) != len(mapped):
            raise ValueError("Duplicate finding mappings")
        if not set(mapped) <= set(self.finding_ids):
            raise ValueError("Mapping refers to an unselected finding")
        return self


class RecordedSkillCoverage(StrEnum):
    ALL = "ALL_REQUIRED_SKILLS_RECORDED"
    SOME = "SOME_REQUIRED_SKILLS_RECORDED"
    NONE = "NO_REQUIRED_SKILLS_RECORDED"
    NOT_EVALUATED = "NOT_EVALUATED"


class NonEvaluationReason(StrEnum):
    UNREVIEWED = "UNREVIEWED_FINDING"
    REJECTED = "REJECTED_FINDING"
    INVALID_QUOTE = "INVALID_SOURCE_QUOTE"
    MISSING_EVIDENCE = "MISSING_SOURCE_EVIDENCE"
    UNMAPPED = "NO_EXPLICIT_SKILL_MAPPING"


class LimitationCode(StrEnum):
    SKILLS_UNVERIFIED = "ASSIGNMENTS_NOT_VERIFIED_QUALIFICATION"
    PROFICIENCY_NOT_DURATION = "PROFICIENCY_NOT_EXPERIENCE_DURATION"
    CERTIFICATIONS_UNVERIFIED = "CERTIFICATIONS_AND_REFERENCES_NOT_VERIFIED"
    REVIEW_NOT_FULFILLMENT = "REVIEW_NOT_REQUIREMENT_FULFILLMENT"
    MISSING_NOT_ABSENCE = "MISSING_ASSIGNMENT_NOT_SKILL_ABSENCE"
    CAPACITY_NOT_EVALUATED = "CAPACITY_AND_COMMITMENT_NOT_EVALUATED"
    REVIEWED_MEANING_UNVERIFIED = "CHANGED_REVIEWED_MEANING_NOT_SOURCE_VERIFIED"


class EvidenceLimitation(MatchingModel):
    schema_version: Literal["capability-evidence-limitation-1"] = "capability-evidence-limitation-1"
    code: LimitationCode
    explanation: str


class CandidateSkillCoverage(MatchingModel):
    schema_version: Literal["candidate-skill-coverage-1"] = "candidate-skill-coverage-1"
    person_id: UUID
    engagement_type: EngagementType
    required_skill_ids: tuple[UUID, ...]
    recorded_assigned_skill_ids: tuple[UUID, ...]
    matched_skill_ids: tuple[UUID, ...]
    missing_assignment_ids: tuple[UUID, ...]
    # Preserve original source/proficiency records; no threshold or duration conversion.
    recorded_assignments: tuple[AssignedSkill, ...]
    coverage: RecordedSkillCoverage
    qualification: Literal["NOT_VERIFIED"] = "NOT_VERIFIED"
    capacity_evaluation: Literal["NOT_EVALUATED"] = "NOT_EVALUATED"


class FindingSkillCoverage(MatchingModel):
    analysis_id: Identifier
    finding_id: Identifier
    original_value: ReviewedValue
    reviewed_value: ReviewedValue
    review_status: ReviewStatus
    grounding_status: GroundingStatus
    source_references: tuple[EvidenceRef, ...]
    reviewed_meaning_verification: Literal["NOT_ASSESSED"] = "NOT_ASSESSED"
    mapping: RequirementSkillMapping | None
    # Summary is about individual coverage, never a union/selection of a potential team.
    coverage_basis: Literal["INDIVIDUAL_ASSIGNMENTS"] = "INDIVIDUAL_ASSIGNMENTS"
    coverage: RecordedSkillCoverage
    non_evaluation_reasons: tuple[NonEvaluationReason, ...]
    candidates: tuple[CandidateSkillCoverage, ...]
    qualification: Literal["NOT_VERIFIED"] = "NOT_VERIFIED"
    capacity_evaluation: Literal["NOT_EVALUATED"] = "NOT_EVALUATED"
    limitations: tuple[EvidenceLimitation, ...]


class CapabilityMatchingResult(MatchingModel):
    schema_version: Literal["capability-matching-result-1"] = "capability-matching-result-1"
    algorithm_version: Literal["explicit-uuid-membership-1"] = MATCHING_ALGORITHM_VERSION
    request: CapabilityMatchingRequest
    analysis_id: Identifier
    snapshot_hash: SnapshotHash
    assessment_period: AssessmentPeriod
    snapshot_schema_version: Literal["1.0"] = "1.0"
    snapshot_source_system: Literal["team-capacity-engine"] = "team-capacity-engine"
    snapshot_source_limitations: tuple[str, ...]
    findings: tuple[FindingSkillCoverage, ...]
    qualification: Literal["NOT_VERIFIED"] = "NOT_VERIFIED"
    capacity_evaluation: Literal["NOT_EVALUATED"] = "NOT_EVALUATED"
