"""Operator declarations, separately versioned from source evidence and skill coverage."""

import hashlib
import json
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from tendercite.domain.capability_matching import SnapshotHash
from tendercite.domain.models import RequirementCategory, RequirementType, ReviewedValue

LocalId = Annotated[str, StringConstraints(min_length=1, max_length=128, pattern=r"^\S+$")]
Rationale = Annotated[str, StringConstraints(min_length=1, max_length=2000, pattern=r"\S")]
OperatorAlias = Annotated[str, StringConstraints(pattern=r"^[a-zA-Z0-9_-]{1,64}$")]
Revision = Annotated[int, Field(ge=1)]
Skills = Annotated[tuple[UUID, ...], Field(min_length=1, max_length=100)]


class MappingModel(BaseModel):
    model_config = ConfigDict(
        strict=True,
        extra="forbid",
        frozen=True,
        hide_input_in_errors=True,
        revalidate_instances="always",
    )


class RequirementInterpretation(MappingModel):
    statement: Annotated[str, StringConstraints(min_length=1, max_length=4000, pattern=r"\S")]
    category: RequirementCategory
    requirement_type: RequirementType


def reviewed_value_digest(value: RequirementInterpretation | ReviewedValue) -> str:
    raw = json.dumps(
        value.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(raw).hexdigest()


class RequirementReviewBinding(MappingModel):
    review_event_id: LocalId | None
    value: RequirementInterpretation
    digest: SnapshotHash

    @model_validator(mode="after")
    def valid_digest(self) -> Self:
        if self.digest != reviewed_value_digest(self.value):
            raise ValueError("Reviewed-value digest mismatch")
        return self


class MappingLifecycle(StrEnum):
    DRAFT = "DRAFT"
    APPROVED = "APPROVED"
    REVOKED = "REVOKED"


class MappingStatus(StrEnum):
    NOT_FOUND = "NOT_FOUND"
    DRAFT = "DRAFT"
    APPROVED = "APPROVED"
    REVOKED = "REVOKED"
    STALE = "STALE"


class MappingUnavailableReason(StrEnum):
    NO_MAPPING = "NO_PERSISTED_MAPPING"
    DRAFT = "MAPPING_NOT_APPROVED"
    REVOKED = "MAPPING_REVOKED"
    REVIEW_CHANGED = "REQUIREMENT_REVIEW_EVENT_CHANGED"
    VALUE_CHANGED = "REQUIREMENT_INTERPRETATION_CHANGED"
    INELIGIBLE_REVIEW = "REQUIREMENT_NOT_REVIEWED_OR_REJECTED"
    INVALID_GROUNDING = "SOURCE_QUOTE_NOT_VERIFIED"


class SkillSelection(MappingModel):
    operator: Literal["ALL_OF"]
    skill_ids: Skills

    @model_validator(mode="after")
    def canonical_skills(self) -> Self:
        if len(set(self.skill_ids)) != len(self.skill_ids):
            raise ValueError("Duplicate skill UUIDs")
        object.__setattr__(self, "skill_ids", tuple(sorted(self.skill_ids)))
        return self


class MappingDraftInput(SkillSelection):
    schema_version: Literal["capability-mapping-draft-1"] = "capability-mapping-draft-1"
    analysis_id: LocalId
    finding_id: LocalId
    snapshot_hash: SnapshotHash
    expected_review_event_id: LocalId | None
    expected_review_digest: SnapshotHash
    rationale: Rationale
    operator_alias: OperatorAlias | None = None


class MappingRevisionInput(SkillSelection):
    schema_version: Literal["capability-mapping-revision-1"] = "capability-mapping-revision-1"
    mapping_id: UUID
    expected_revision: Revision
    expected_review_event_id: LocalId | None
    expected_review_digest: SnapshotHash
    rationale: Rationale
    operator_alias: OperatorAlias | None = None


class MappingRevocationInput(MappingModel):
    schema_version: Literal["capability-mapping-revocation-1"] = "capability-mapping-revocation-1"
    mapping_id: UUID
    expected_revision: Revision
    rationale: Rationale
    operator_alias: OperatorAlias | None = None


class MappingApprovalInput(MappingModel):
    schema_version: Literal["capability-mapping-approval-1"] = "capability-mapping-approval-1"
    mapping_id: UUID
    expected_revision: Revision
    expected_review_event_id: LocalId
    expected_review_digest: SnapshotHash
    rationale: Rationale
    operator_alias: OperatorAlias | None = None


class CapabilityMappingRecord(SkillSelection):
    schema_version: Literal["capability-mapping-record-1"] = "capability-mapping-record-1"
    matching_contract_version: Literal["requirement-skill-mapping-1"] = (
        "requirement-skill-mapping-1"
    )
    mapping_id: UUID
    analysis_id: LocalId
    finding_id: LocalId
    snapshot_hash: SnapshotHash
    review_binding: RequirementReviewBinding
    revision: Revision
    status: MappingLifecycle
    created_at: datetime
    recorded_at: datetime
    approved_at: datetime | None
    rationale: Rationale
    operator_alias: OperatorAlias | None = None

    @model_validator(mode="after")
    def valid_metadata(self) -> Self:
        dates = (self.created_at, self.recorded_at)
        if self.approved_at is not None:
            dates += (self.approved_at,)
        if any(d.utcoffset() != timedelta(0) for d in dates):
            raise ValueError("UTC timestamps required")
        if self.recorded_at < self.created_at:
            raise ValueError("Invalid revision timestamp")
        if self.status == MappingLifecycle.APPROVED:
            if self.approved_at != self.recorded_at or self.review_binding.review_event_id is None:
                raise ValueError("Invalid approval metadata")
        elif self.approved_at is not None:
            raise ValueError("Only approval revisions have approval timestamps")
        return self


class MappingInspection(MappingModel):
    schema_version: Literal["capability-mapping-inspection-1"] = "capability-mapping-inspection-1"
    analysis_id: LocalId
    finding_id: LocalId
    snapshot_hash: SnapshotHash
    status: MappingStatus
    record: CapabilityMappingRecord | None
    reasons: tuple[MappingUnavailableReason, ...]


class MappingErrorCode(StrEnum):
    INVALID_INPUT = "INVALID_MAPPING_INPUT"
    NOT_FOUND = "MAPPING_NOT_FOUND"
    ALREADY_EXISTS = "MAPPING_ALREADY_EXISTS"
    CONFLICT = "MAPPING_REVISION_CONFLICT"
    REVIEW_CONFLICT = "REQUIREMENT_REVIEW_CONFLICT"
    INELIGIBLE = "REQUIREMENT_NOT_ELIGIBLE"
    UNKNOWN_SKILL = "UNKNOWN_CATALOG_SKILL_UUID"
    INVALID_TRANSITION = "INVALID_MAPPING_TRANSITION"
    INVALID_SCOPE = "INVALID_MAPPING_SCOPE"
    SNAPSHOT_NOT_FOUND = "SNAPSHOT_NOT_FOUND"
    STORAGE_INTEGRITY = "MAPPING_STORAGE_INTEGRITY"
    DATABASE_FAILED = "DATABASE_FAILED"


class CapabilityMappingError(ValueError):
    def __init__(self, code: MappingErrorCode):
        self.code = code
        super().__init__(code.value)
