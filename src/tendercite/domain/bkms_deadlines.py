"""Allowlisted official submission metadata, separate from OCDS/native opportunity fields."""

from datetime import date
from typing import Annotated, Literal

from pydantic import AwareDatetime, Field, StringConstraints, model_validator

from tendercite.domain.bkms_reference import BkmsModel, Digest, ExternalId, notice_parts

MAX_DEADLINE_BYTES = 8 * 1024 * 1024
DeadlineStatus = Literal["SINGLE", "MULTIPLE_LOTS", "MISSING", "CONFLICT", "UNRECOGNIZED"]
RawValue = Annotated[str, StringConstraints(max_length=40)]
DeadlineWarning = Literal[
    "UNSUPPORTED_CUSTOMIZATION",
    "UNRECOGNIZED_STRUCTURE",
    "MISSING_SUBMISSION_DEADLINE",
    "MISSING_TIME",
    "MISSING_TIMEZONE",
    "OFFSET_CONFLICT",
    "CONFLICTING_VALUES",
    "DUPLICATE_LOT",
]


class SourcePeriod(BkmsModel):
    # Preserve lists instead of choosing one value if a nonrepeatable XML field repeats.
    dates: Annotated[tuple[RawValue, ...], Field(max_length=4)]
    times: Annotated[tuple[RawValue, ...], Field(max_length=4)]


class LotDeadline(BkmsModel):
    lot_id: Annotated[str, StringConstraints(min_length=1, max_length=50)]
    status: DeadlineStatus
    periods: Annotated[tuple[SourcePeriod, ...], Field(max_length=4)]


class DeadlineNotice(BkmsModel):
    external_id: ExternalId
    record_sha256: Digest
    customization_id: Annotated[str, StringConstraints(max_length=80)]
    status: DeadlineStatus
    lots: Annotated[tuple[LotDeadline, ...], Field(max_length=128)]
    warnings: Annotated[tuple[DeadlineWarning, ...], Field(max_length=8)]

    @model_validator(mode="after")
    def identity(self):
        notice_parts(self.external_id)
        if len(set(self.warnings)) != len(self.warnings):
            raise ValueError("Duplicate warnings")
        return self


class DeadlineCatalog(BkmsModel):
    schema_version: Literal["bkms-eforms-deadlines-1"] = "bkms-eforms-deadlines-1"
    catalog_id: Digest
    export_day: date
    retrieved_at: AwareDatetime
    archive_sha256: Digest
    license: Literal["https://opendefinition.org/licenses/cc-zero/"] = (
        "https://opendefinition.org/licenses/cc-zero/"
    )
    publication_policy: Literal["https://oeffentlichevergabe.de/ui/de/publicationPolicy"] = (
        "https://oeffentlichevergabe.de/ui/de/publicationPolicy"
    )
    records: Annotated[tuple[DeadlineNotice, ...], Field(max_length=4000)]

    @model_validator(mode="after")
    def unique(self):
        if len({r.external_id for r in self.records}) != len(self.records):
            raise ValueError("Duplicate notice identity")
        return self


class DeadlineLoadInput(BkmsModel):
    schema_version: Literal["bkms-deadline-load-1"] = "bkms-deadline-load-1"
    catalog_id: Digest
    expected_enrichment_id: Digest | None = None


class DeadlineSummary(BkmsModel):
    schema_version: Literal["bkms-deadline-summary-1"] = "bkms-deadline-summary-1"
    state: Literal["NOT_LOADED", "CURRENT", "STALE"]
    catalog_id: Digest
    export_day: date
    enrichment_id: Digest | None = None
    bound_catalog_id: Digest | None = None
    archive_sha256: Digest | None = None
    retrieved_at: AwareDatetime | None = None
    extraction_version: Literal["bkms-eforms-deadlines-1"] = "bkms-eforms-deadlines-1"
    counts: Annotated[
        dict[DeadlineStatus, Annotated[int, Field(ge=1, le=4000)]], Field(max_length=5)
    ]


class DeadlineEvidence(BkmsModel):
    enrichment_id: Digest
    catalog_id: Digest
    export_day: date
    archive_sha256: Digest
    retrieved_at: AwareDatetime
    extraction_version: Literal["bkms-eforms-deadlines-1"] = "bkms-eforms-deadlines-1"
    notice: DeadlineNotice
