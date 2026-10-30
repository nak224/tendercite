"""Native, user-declared capabilities; independent of workforce snapshots and tender findings."""

import unicodedata
from enum import StrEnum
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)

MAX_PROFILE_BYTES = 65536
Name = Annotated[str, StringConstraints(min_length=1, max_length=120, pattern=r"\S")]
SkillName = Annotated[str, StringConstraints(min_length=1, max_length=100, pattern=r"\S")]
Keyword = Annotated[str, StringConstraints(min_length=1, max_length=80, pattern=r"\S")]
Revision = Annotated[int, Field(ge=1, le=2147483647)]


class ProfileModel(BaseModel):
    model_config = ConfigDict(
        strict=True,
        extra="forbid",
        frozen=True,
        hide_input_in_errors=True,
        revalidate_instances="always",
        str_strip_whitespace=True,
    )


def normalized_label(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


class ProfileType(StrEnum):
    FREELANCER = "FREELANCER"
    COMPANY = "COMPANY"


class WorkArrangement(StrEnum):
    REMOTE = "REMOTE"
    HYBRID = "HYBRID"
    ON_SITE = "ON_SITE"


class ProfileSkill(ProfileModel):
    skill_id: UUID
    name: SkillName
    source_origin: Literal["manual_operator_entry"] = "manual_operator_entry"
    verification_status: Literal["NOT_VERIFIED"] = "NOT_VERIFIED"


class NativeCapabilityProfile(ProfileModel):
    schema_version: Literal["native-capability-profile-1"] = "native-capability-profile-1"
    name: Name
    profile_type: ProfileType
    headline: Annotated[str, StringConstraints(max_length=300)] = ""
    location: Annotated[str, StringConstraints(max_length=120)] = ""
    work_arrangement: WorkArrangement | None = None
    skills: Annotated[tuple[ProfileSkill, ...], Field(max_length=100)] = ()
    opportunity_keywords: Annotated[tuple[Keyword, ...], Field(max_length=20)] = ()
    source_origin: Literal["manual_operator_entry"] = "manual_operator_entry"
    qualification: Literal["NOT_VERIFIED"] = "NOT_VERIFIED"

    @model_validator(mode="after")
    def unique_declarations(self) -> Self:
        ids = [skill.skill_id for skill in self.skills]
        labels = [normalized_label(skill.name) for skill in self.skills]
        keywords = [normalized_label(word) for word in self.opportunity_keywords]
        if len(set(ids)) != len(ids) or len(set(labels)) != len(labels):
            raise ValueError("Duplicate skills")
        if len(set(keywords)) != len(keywords):
            raise ValueError("Duplicate preferences")
        return self


class ProfileCreate(ProfileModel):
    schema_version: Literal["native-profile-create-1"] = "native-profile-create-1"
    profile_id: UUID
    profile: NativeCapabilityProfile


class ProfileUpdate(ProfileModel):
    schema_version: Literal["native-profile-update-1"] = "native-profile-update-1"
    expected_revision: Revision
    profile: NativeCapabilityProfile


class ProfileRecord(ProfileModel):
    schema_version: Literal["native-profile-record-1"] = "native-profile-record-1"
    profile_id: UUID
    revision: Revision
    created_at: AwareDatetime
    updated_at: AwareDatetime
    profile: NativeCapabilityProfile

    @model_validator(mode="after")
    def timestamp_order(self) -> Self:
        if self.updated_at < self.created_at:
            raise ValueError("Invalid record timestamps")
        return self


class ProfileSaveResult(ProfileModel):
    schema_version: Literal["native-profile-save-result-1"] = "native-profile-save-result-1"
    status: Literal["CREATED", "ALREADY_PRESENT", "UPDATED"]
    record: ProfileRecord


class ProfileMetadata(ProfileModel):
    profile_id: UUID
    revision: Revision
    name: Name
    profile_type: ProfileType
    updated_at: AwareDatetime


class ProfileListing(ProfileModel):
    schema_version: Literal["native-profile-listing-1"] = "native-profile-listing-1"
    profiles: Annotated[tuple[ProfileMetadata, ...], Field(max_length=100)]
    next_offset: Annotated[int, Field(ge=0)] | None


class ProfileErrorCode(StrEnum):
    INVALID_INPUT = "INVALID_PROFILE_INPUT"
    NOT_FOUND = "PROFILE_NOT_FOUND"
    ID_CONFLICT = "PROFILE_ID_CONFLICT"
    REVISION_CONFLICT = "PROFILE_REVISION_CONFLICT"
    STORAGE_INTEGRITY = "PROFILE_STORAGE_INTEGRITY"
    DATABASE_FAILED = "DATABASE_FAILED"


class ProfileError(ValueError):
    def __init__(self, code: ProfileErrorCode):
        self.code = code
        super().__init__(code.value)
