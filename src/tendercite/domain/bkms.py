"""One explicitly loaded daily catalog; no remote search or cross-source identity inference."""

from datetime import date, datetime
from typing import Annotated, Literal
from zoneinfo import ZoneInfo

from pydantic import AwareDatetime, Field, StringConstraints, field_validator, model_validator

from tendercite.domain.bkms_reference import BkmsModel, BkmsSource, Digest, ExternalId
from tendercite.domain.opportunities import NativeOpportunity, OpportunityType
from tendercite.domain.ted_reference import literal_keyword

MAX_RECORDS = 4000
MAX_CATALOG_BYTES = 48 * 1024 * 1024


class BkmsError(RuntimeError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class CatalogContents(BkmsModel):
    schema_version: Literal["bkms-catalog-1"] = "bkms-catalog-1"
    export_day: date
    archive_sha256: Digest
    retrieved_at: AwareDatetime
    records: Annotated[tuple[BkmsSource, ...], Field(max_length=MAX_RECORDS)]

    @model_validator(mode="after")
    def consistent(self):
        if len({r.external_id for r in self.records}) != len(self.records) or any(
            r.export_day != self.export_day
            or r.archive_sha256 != self.archive_sha256
            or r.retrieved_at != self.retrieved_at
            for r in self.records
        ):
            raise ValueError("Inconsistent catalog")
        return self


class CatalogSummary(BkmsModel):
    schema_version: Literal["bkms-catalog-summary-1"] = "bkms-catalog-summary-1"
    catalog_id: Digest
    export_day: date
    archive_sha256: Digest
    retrieved_at: AwareDatetime
    record_count: Annotated[int, Field(ge=0, le=MAX_RECORDS)]
    importable_count: Annotated[int, Field(ge=0, le=MAX_RECORDS)]
    warning_count: Annotated[int, Field(ge=0)]


class LoadInput(BkmsModel):
    schema_version: Literal["bkms-load-input-1"] = "bkms-load-input-1"
    export_day: date

    @field_validator("export_day")
    @classmethod
    def historical_day(cls, value):
        if not date(2022, 12, 1) <= value < datetime.now(ZoneInfo("Europe/Berlin")).date():
            raise ValueError("Select one completed publication day")
        return value


class SearchInput(BkmsModel):
    schema_version: Literal["bkms-search-input-1"] = "bkms-search-input-1"
    catalog_id: Digest
    keyword: Annotated[str, StringConstraints(min_length=1, max_length=150)]
    page: Annotated[int, Field(ge=1, le=4000)] = 1
    limit: Annotated[int, Field(ge=1, le=20)] = 10

    @field_validator("keyword")
    @classmethod
    def phrase(cls, value):
        return literal_keyword(value)


class ImportInput(BkmsModel):
    schema_version: Literal["bkms-import-input-1"] = "bkms-import-input-1"
    catalog_id: Digest
    external_id: ExternalId


def mapped_text(value: str | None, size: int) -> str | None:
    return value.strip()[:size] or None if value is not None else None


def native_opportunity(source: BkmsSource) -> NativeOpportunity:
    meta = source.original_metadata
    return NativeOpportunity(
        title=mapped_text(meta.title, 200),
        opportunity_type=OpportunityType.PUBLIC_TENDER,
        organization=mapped_text(meta.buyer, 200) or "",
        description=mapped_text(meta.description, 2000) or "",
        deadline=date.fromisoformat(meta.deadline[:10]) if meta.deadline else None,
        source_platform="Bekanntmachungsservice",
        source_url=source.original_url,
        source_origin="bkms_import",
        external_source=source,
    )


class SearchResult(BkmsModel):
    schema_version: Literal["bkms-search-result-1"] = "bkms-search-result-1"
    catalog: CatalogSummary
    keyword: str
    page: int
    limit: int
    total_count: int
    items: Annotated[tuple[BkmsSource, ...], Field(max_length=20)]
    next_page: int | None
