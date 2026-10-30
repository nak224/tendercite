"""Versioned label-comparison contracts, separate from workforce qualification workflows."""

from enum import StrEnum
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from tendercite.domain.opportunities import OpportunityType
from tendercite.domain.profiles import ProfileType

Revision = Annotated[int, Field(ge=1, le=2147483647)]
Label = Annotated[str, StringConstraints(min_length=1, max_length=100, pattern=r"\S")]
NormalizedLabel = Annotated[str, StringConstraints(min_length=1, max_length=4096, pattern=r"\S")]
Count = Annotated[int, Field(ge=0, le=100)]


class NativeMatchModel(BaseModel):
    model_config = ConfigDict(
        strict=True,
        extra="forbid",
        frozen=True,
        hide_input_in_errors=True,
        revalidate_instances="always",
    )


class NativeMatchSelection(NativeMatchModel):
    schema_version: Literal["native-match-selection-1"] = "native-match-selection-1"
    profile_id: UUID
    opportunity_id: UUID
    expected_profile_revision: Revision
    expected_opportunity_revision: Revision


class ProfileLabel(NativeMatchModel):
    skill_id: UUID
    original_label: Label
    normalized_label: NormalizedLabel


class ComparedProfile(NativeMatchModel):
    profile_id: UUID
    revision: Revision
    name: Annotated[str, StringConstraints(min_length=1, max_length=120)]
    profile_type: ProfileType
    skills: Annotated[tuple[ProfileLabel, ...], Field(max_length=100)]


class RequiredLabel(NativeMatchModel):
    original_label: Label
    normalized_label: NormalizedLabel
    status: Literal["MATCHED", "NOT_RECORDED"]
    matched_profile_skill_ids: Annotated[tuple[UUID, ...], Field(max_length=100)]


class ComparedOpportunity(NativeMatchModel):
    opportunity_id: UUID
    revision: Revision
    title: Annotated[str, StringConstraints(min_length=1, max_length=200)]
    opportunity_type: OpportunityType
    organization: Annotated[str, StringConstraints(max_length=200)]
    required_skills: Annotated[tuple[RequiredLabel, ...], Field(max_length=100)]


class NativeMatchCounts(NativeMatchModel):
    total_opportunity_skills: Count
    matched_skills: Count
    not_recorded_skills: Count


EVIDENCE_LIMITATIONS = (
    "Results compare manually entered skill labels only; "
    "the declarations are not independently verified.",
    "NOT_RECORDED means no equal normalized declaration was found, not that a skill is absent.",
    "Experience, qualifications, certifications, availability and eligibility are not verified.",
    "No suitability judgment, ranking, percentage score, "
    "tender fulfillment or Go/No-Go is inferred.",
    "Only profile skill names and opportunity required-skill labels are compared; "
    "other fields are ignored.",
)


class NativeMatchResult(NativeMatchModel):
    schema_version: Literal["native-match-result-1"] = "native-match-result-1"
    algorithm_version: Literal["nfkc-casefold-whitespace-equality-1"] = (
        "nfkc-casefold-whitespace-equality-1"
    )
    status: Literal["COMPARED", "NO_REQUIREMENTS"]
    profile: ComparedProfile
    opportunity: ComparedOpportunity
    counts: NativeMatchCounts
    evidence_limitations: tuple[str, ...] = EVIDENCE_LIMITATIONS
    qualification: Literal["NOT_VERIFIED"] = "NOT_VERIFIED"
    availability: Literal["NOT_EVALUATED"] = "NOT_EVALUATED"
    eligibility: Literal["NOT_VERIFIED"] = "NOT_VERIFIED"


class NativeMatchErrorCode(StrEnum):
    INVALID_INPUT = "INVALID_NATIVE_MATCH_INPUT"
    PROFILE_NOT_FOUND = "PROFILE_NOT_FOUND"
    OPPORTUNITY_NOT_FOUND = "OPPORTUNITY_NOT_FOUND"
    PROFILE_CONFLICT = "PROFILE_REVISION_CONFLICT"
    OPPORTUNITY_CONFLICT = "OPPORTUNITY_REVISION_CONFLICT"
    PROFILE_INTEGRITY = "PROFILE_STORAGE_INTEGRITY"
    OPPORTUNITY_INTEGRITY = "OPPORTUNITY_STORAGE_INTEGRITY"
    DATABASE_FAILED = "DATABASE_FAILED"


class NativeMatchError(ValueError):
    def __init__(self, code: NativeMatchErrorCode):
        self.code = code
        super().__init__(code.value)
