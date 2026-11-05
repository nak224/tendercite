"""Bounded collection/search contracts; original daily catalogs and source identities stay v1."""

from datetime import date
from typing import Annotated, Literal

from pydantic import Field, StringConstraints, field_validator

from tendercite.domain.bkms import CatalogSummary
from tendercite.domain.bkms_reference import BkmsModel, BkmsSource, Digest, WarningCode
from tendercite.domain.ted_reference import literal_keyword

MAX_DAYS = 7
MAX_TOTAL_BYTES = 96 * 1024 * 1024
MAX_RESULTS = 7 * 4000
NoticeCategory = Literal["TENDER", "AWARD", "PLANNING", "OTHER_UNKNOWN"]
CategoryFilter = Literal["ALL", "TENDER", "AWARD", "PLANNING", "OTHER_UNKNOWN"]

# Exact codes from the official OCDS 1.1 releaseTag codelist. These group related
# publication types, including cancellations; they do not determine procurement status.
TENDER_TAGS = frozenset({"tender", "tenderAmendment", "tenderUpdate", "tenderCancellation"})
AWARD_TAGS = frozenset({"award", "awardUpdate", "awardCancellation"})
PLANNING_TAGS = frozenset({"planning", "planningUpdate"})


def notice_category(tags: tuple[str, ...]) -> NoticeCategory:
    values = set(tags)
    if values and values <= TENDER_TAGS:
        return "TENDER"
    if values and values <= AWARD_TAGS:
        return "AWARD"
    if values and values <= PLANNING_TAGS:
        return "PLANNING"
    return "OTHER_UNKNOWN"


class StoredDay(BkmsModel):
    catalog: CatalogSummary
    warning_counts: Annotated[
        dict[WarningCode, Annotated[int, Field(ge=1, le=4000)]], Field(max_length=9)
    ]


class CatalogCollection(BkmsModel):
    schema_version: Literal["bkms-catalog-collection-1"] = "bkms-catalog-collection-1"
    collection_id: Digest
    days: Annotated[tuple[StoredDay, ...], Field(max_length=MAX_DAYS)]
    stored_bytes: Annotated[int, Field(ge=0, le=MAX_TOTAL_BYTES)]
    max_days: Literal[7] = MAX_DAYS
    max_storage_bytes: Literal[100663296] = MAX_TOTAL_BYTES


class MultiSearchInput(BkmsModel):
    schema_version: Literal["bkms-search-input-2"] = "bkms-search-input-2"
    collection_id: Digest
    keyword: Annotated[str, StringConstraints(min_length=1, max_length=150)]
    page: Annotated[int, Field(ge=1, le=MAX_RESULTS)] = 1
    limit: Annotated[int, Field(ge=1, le=20)] = 10
    notice_category: CategoryFilter = "ALL"
    include_earlier_versions: bool = False

    @field_validator("keyword")
    @classmethod
    def phrase(cls, value):
        return literal_keyword(value)


class RemoveInput(BkmsModel):
    schema_version: Literal["bkms-remove-input-1"] = "bkms-remove-input-1"
    export_day: date
    expected_catalog_id: Digest


class NoticeOccurrence(BkmsModel):
    export_day: date
    catalog_id: Digest
    record_sha256: Digest


class MultiSearchHit(BkmsModel):
    source: BkmsSource
    notice_category: NoticeCategory
    version_count: Annotated[int, Field(ge=1, le=MAX_RESULTS)]
    is_latest_stored: bool
    occurrences: Annotated[tuple[NoticeOccurrence, ...], Field(min_length=1, max_length=MAX_DAYS)]
    source_record_hashes_differ: bool


class MultiSearchResult(BkmsModel):
    schema_version: Literal["bkms-search-result-2"] = "bkms-search-result-2"
    coverage: CatalogCollection
    keyword: str
    page: int
    limit: int
    notice_category: CategoryFilter
    include_earlier_versions: bool
    total_count: Annotated[int, Field(ge=0, le=MAX_RESULTS)]
    items: Annotated[tuple[MultiSearchHit, ...], Field(max_length=20)]
    next_page: int | None
