"""Bekanntmachungsservice identities and immutable, allowlisted Open Data provenance."""

import re
from datetime import date, datetime
from typing import Annotated, Literal, Self
from urllib.parse import parse_qs, urlsplit
from uuid import NAMESPACE_URL, UUID, uuid5

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

ORIGIN = "https://oeffentlichevergabe.de"
POLICY = ORIGIN + "/ui/de/publicationPolicy"
LICENSE = "https://opendefinition.org/licenses/cc-zero/"
Digest = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
ExternalId = Annotated[
    str, StringConstraints(pattern=r"^(?:[0-9a-f-]{36}|[0-9]{1,20})-[0-9]{1,10}$")
]
WarningCode = Literal[
    "MISSING_TITLE",
    "MISSING_BUYER",
    "MISSING_DESCRIPTION",
    "MISSING_PUBLICATION_DATE",
    "MISSING_DEADLINE",
    "TITLE_SHORTENED",
    "ORGANIZATION_SHORTENED",
    "SUMMARY_SHORTENED",
    "SOURCE_METADATA_SHORTENED",
]


def notice_parts(identity: str) -> tuple[str, str]:
    if not isinstance(identity, str) or not re.fullmatch(
        r"(?:[0-9a-f-]{36}|[0-9]{1,20})-[0-9]{1,10}", identity
    ):
        raise ValueError("Invalid publication identity")
    notice_id, version = identity.rsplit("-", 1)
    if not notice_id.isdigit() and str(UUID(notice_id)) != notice_id:
        raise ValueError("Noncanonical publication identity")
    return notice_id, version


def notice_url(identity: str) -> str:
    notice_id, version = notice_parts(identity)
    return f"{ORIGIN}/ui/notices/{notice_id}?noticeVersion={version}"


def validate_record_url(value: str, identity: str) -> str:
    notice_id, version = notice_parts(identity)
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or parsed.netloc != "oeffentlichevergabe.de"
        or parsed.path != f"/api/notices/{notice_id}"
        or parsed.fragment
        or parse_qs(parsed.query, keep_blank_values=True)
        != {"format": ["ocds"], "noticeVersion": [version]}
    ):
        raise ValueError("Invalid official record reference")
    return value


def opportunity_id(identity: str):
    notice_parts(identity)
    return uuid5(NAMESPACE_URL, notice_url(identity))


class BkmsModel(BaseModel):
    model_config = ConfigDict(
        strict=True,
        extra="forbid",
        frozen=True,
        hide_input_in_errors=True,
        revalidate_instances="always",
    )


class OriginalMetadata(BkmsModel):
    # Preserve source strings, including whitespace; never retain parties/contact records.
    title: Annotated[str, StringConstraints(max_length=1000)] | None
    buyer: Annotated[str, StringConstraints(max_length=500)] | None
    description: Annotated[str, StringConstraints(max_length=8000)] | None
    published_at: Annotated[str, StringConstraints(max_length=40)] | None
    deadline: Annotated[str, StringConstraints(max_length=40)] | None
    tags: Annotated[
        tuple[Annotated[str, StringConstraints(min_length=1, max_length=50)], ...],
        Field(max_length=20),
    ]

    @field_validator("published_at", "deadline")
    @classmethod
    def source_timestamp(cls, value):
        if value is not None:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                raise ValueError("Source timestamp requires an offset")
        return value


class BkmsSource(BkmsModel):
    schema_version: Literal["bkms-opportunity-source-1"] = "bkms-opportunity-source-1"
    source: Literal["BEKANNTMACHUNGSSERVICE"] = "BEKANNTMACHUNGSSERVICE"
    external_id: ExternalId
    original_url: Annotated[str, StringConstraints(max_length=2048)]
    record_url: Annotated[str, StringConstraints(max_length=2048)]
    ocid: Annotated[str, StringConstraints(max_length=150)] | None
    export_day: date
    retrieved_at: AwareDatetime
    archive_sha256: Digest
    record_sha256: Digest
    license: Literal["https://opendefinition.org/licenses/cc-zero/"] = LICENSE
    publication_policy: Literal["https://oeffentlichevergabe.de/ui/de/publicationPolicy"] = POLICY
    original_metadata: OriginalMetadata
    mapping_warnings: Annotated[tuple[WarningCode, ...], Field(max_length=12)]

    @model_validator(mode="after")
    def references(self) -> Self:
        if self.original_url != notice_url(self.external_id):
            raise ValueError("Invalid publication reference")
        validate_record_url(self.record_url, self.external_id)
        if len(set(self.mapping_warnings)) != len(self.mapping_warnings):
            raise ValueError("Duplicate warnings")
        return self
