"""One bounded official TED source; metadata is not verified tender requirement evidence."""

from datetime import date
from typing import Annotated, Literal, Self

from pydantic import AwareDatetime, Field, StringConstraints, field_validator, model_validator

from tendercite.domain.opportunities import OpportunityModel
from tendercite.domain.ted_reference import literal_keyword, publication_id, validate_notice_url


class TedSearchInput(OpportunityModel):
    schema_version: Literal["ted-search-input-1"] = "ted-search-input-1"
    keyword: Annotated[str, StringConstraints(min_length=1, max_length=150)]
    page: Annotated[int, Field(ge=1, le=50)] = 1
    limit: Annotated[int, Field(ge=1, le=20)] = 10

    @field_validator("keyword")
    @classmethod
    def literal_phrase(cls, value):
        return literal_keyword(value)


class TedDiscoveryItem(OpportunityModel):
    publication_id: Annotated[str, StringConstraints(max_length=13)]
    source: Literal["TED"] = "TED"
    title: Annotated[str, StringConstraints(max_length=200)] | None
    organization: Annotated[str, StringConstraints(max_length=200)] | None
    summary: Annotated[str, StringConstraints(max_length=2000)] | None
    publication_date: date | None
    submission_deadline: date | None
    source_url: Annotated[str, StringConstraints(max_length=2048)]
    retrieved_at: AwareDatetime
    importable: bool
    warnings: Annotated[tuple[str, ...], Field(max_length=12)]

    @model_validator(mode="after")
    def canonical_reference(self) -> Self:
        if publication_id(self.publication_id) != self.publication_id:
            raise ValueError("Noncanonical publication identity")
        validate_notice_url(self.source_url, self.publication_id)
        if self.importable != bool(self.title):
            raise ValueError("Missing required source title")
        return self


class TedSearchResult(OpportunityModel):
    schema_version: Literal["ted-search-result-1"] = "ted-search-result-1"
    source: Literal["TED"] = "TED"
    scope: Literal["ACTIVE_COMPETITION"] = "ACTIVE_COMPETITION"
    keyword: str
    page: Annotated[int, Field(ge=1, le=50)]
    limit: Annotated[int, Field(ge=1, le=20)]
    total_notice_count: Annotated[int, Field(ge=0)]
    items: Annotated[tuple[TedDiscoveryItem, ...], Field(max_length=20)]
    next_page: Annotated[int, Field(ge=2, le=50)] | None
    pagination_capped: bool


class TedImportInput(OpportunityModel):
    schema_version: Literal["ted-import-input-1"] = "ted-import-input-1"
    publication_id: Annotated[str, StringConstraints(min_length=6, max_length=13)]

    @field_validator("publication_id")
    @classmethod
    def canonical_id(cls, value):
        return publication_id(value)
