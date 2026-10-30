"""Manual references to external opportunities; never analyzed tenders or hosted listings."""

import unicodedata
from datetime import date
from enum import StrEnum
from typing import Annotated, Literal, Self
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import (
    AnyHttpUrl,
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

MAX_OPPORTUNITY_BYTES = 65536
Title = Annotated[str, StringConstraints(min_length=1, max_length=200, pattern=r"\S")]
Organization = Annotated[str, StringConstraints(max_length=200)]
SkillLabel = Annotated[str, StringConstraints(min_length=1, max_length=100, pattern=r"\S")]
Revision = Annotated[int, Field(ge=1, le=2147483647)]


class OpportunityModel(BaseModel):
    model_config = ConfigDict(
        strict=True,
        extra="forbid",
        frozen=True,
        hide_input_in_errors=True,
        revalidate_instances="always",
        str_strip_whitespace=True,
    )


class OpportunityType(StrEnum):
    PUBLIC_TENDER = "PUBLIC_TENDER"
    FREELANCE_PROJECT = "FREELANCE_PROJECT"
    OTHER = "OTHER"


class WorkArrangement(StrEnum):
    REMOTE = "REMOTE"
    HYBRID = "HYBRID"
    ON_SITE = "ON_SITE"


class NativeOpportunity(OpportunityModel):
    schema_version: Literal["native-opportunity-1"] = "native-opportunity-1"
    title: Title
    opportunity_type: OpportunityType
    organization: Organization = ""
    description: Annotated[str, StringConstraints(max_length=2000)] = ""
    location: Annotated[str, StringConstraints(max_length=120)] = ""
    work_arrangement: WorkArrangement | None = None
    deadline: date | None = None
    source_platform: Annotated[str, StringConstraints(max_length=200)] = ""
    source_url: Annotated[str, StringConstraints(max_length=2048)] = ""
    required_skills: Annotated[tuple[SkillLabel, ...], Field(max_length=100)] = ()
    requirements_description: Annotated[str, StringConstraints(max_length=2000)] = ""
    source_origin: Literal["manual_operator_entry"] = "manual_operator_entry"
    verification_status: Literal["NOT_VERIFIED"] = "NOT_VERIFIED"

    @field_validator("source_url")
    @classmethod
    def http_reference_only(cls, value):
        if not value:
            return value
        if any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in value) or "\\" in value:
            raise ValueError("Invalid HTTP reference")
        parts = urlsplit(value)
        if (
            parts.scheme not in ("http", "https")
            or not parts.netloc
            or parts.username is not None
            or parts.password is not None
        ):
            raise ValueError("Invalid HTTP reference")
        if parts.port == 0:
            raise ValueError("Invalid HTTP reference")
        AnyHttpUrl(value)  # Syntax validation only; never DNS resolution or a network request.
        return value  # Preserve the operator's reference, rather than rewriting a source URL.

    @model_validator(mode="after")
    def unique_skill_labels(self) -> Self:
        labels = [
            " ".join(unicodedata.normalize("NFKC", s).casefold().split())
            for s in self.required_skills
        ]
        if len(labels) != len(set(labels)):
            raise ValueError("Duplicate skill labels")
        return self


class OpportunityCreate(OpportunityModel):
    schema_version: Literal["native-opportunity-create-1"] = "native-opportunity-create-1"
    opportunity_id: UUID
    opportunity: NativeOpportunity


class OpportunityUpdate(OpportunityModel):
    schema_version: Literal["native-opportunity-update-1"] = "native-opportunity-update-1"
    expected_revision: Revision
    opportunity: NativeOpportunity


class OpportunityRecord(OpportunityModel):
    schema_version: Literal["native-opportunity-record-1"] = "native-opportunity-record-1"
    opportunity_id: UUID
    revision: Revision
    created_at: AwareDatetime
    updated_at: AwareDatetime
    opportunity: NativeOpportunity

    @model_validator(mode="after")
    def timestamp_order(self) -> Self:
        if self.updated_at < self.created_at:
            raise ValueError("Invalid record timestamps")
        return self


class OpportunitySaveResult(OpportunityModel):
    schema_version: Literal["native-opportunity-save-result-1"] = "native-opportunity-save-result-1"
    status: Literal["CREATED", "ALREADY_PRESENT", "UPDATED"]
    record: OpportunityRecord


class OpportunityMetadata(OpportunityModel):
    opportunity_id: UUID
    revision: Revision
    title: Title
    opportunity_type: OpportunityType
    organization: Organization
    deadline: date | None
    updated_at: AwareDatetime


class OpportunityListing(OpportunityModel):
    schema_version: Literal["native-opportunity-listing-1"] = "native-opportunity-listing-1"
    opportunities: Annotated[tuple[OpportunityMetadata, ...], Field(max_length=100)]
    next_offset: Annotated[int, Field(ge=0)] | None


class OpportunityErrorCode(StrEnum):
    INVALID_INPUT = "INVALID_OPPORTUNITY_INPUT"
    NOT_FOUND = "OPPORTUNITY_NOT_FOUND"
    ID_CONFLICT = "OPPORTUNITY_ID_CONFLICT"
    REVISION_CONFLICT = "OPPORTUNITY_REVISION_CONFLICT"
    STORAGE_INTEGRITY = "OPPORTUNITY_STORAGE_INTEGRITY"
    DATABASE_FAILED = "DATABASE_FAILED"


class OpportunityError(ValueError):
    def __init__(self, code: OpportunityErrorCode):
        self.code = code
        super().__init__(code.value)
