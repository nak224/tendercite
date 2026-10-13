"""Separate, versioned offline review schemas; never provider response schemas."""

from datetime import datetime
from typing import Literal

from pydantic import Field, field_validator, model_validator

from evaluation.cloud_llm_contexts import Nonblank, StrictModel
from evaluation.external_qa_spans import Alias, Digest

LEGAL_LAYER_VERSION = "staged-legal-reference-layer-1"
NORMALIZATION_VERSION = "source-backed-legal-normalization-1"


class ReviewedAssociation(StrictModel):
    author_alias: Alias
    reviewer_alias: Alias
    reviewed_by_human: Literal[True]
    reviewed_at: datetime
    review_notes: Nonblank
    original_response_sha256s: list[Digest] = Field(min_length=1)

    @field_validator("reviewed_at")
    @classmethod
    def timezone_required(cls, value):
        if value.tzinfo is None:
            raise ValueError("Review timestamp requires a timezone")
        return value

    @model_validator(mode="after")
    def independent_review(self):
        if self.author_alias == self.reviewer_alias:
            raise ValueError("An independent human reviewer is required")
        if len(set(self.original_response_sha256s)) != len(self.original_response_sha256s):
            raise ValueError("Duplicate original artifact hashes")
        return self


class AliasAssociation(ReviewedAssociation):
    alias: Nonblank
    instrument_identifier: Nonblank


class AliasMap(StrictModel):
    schema_version: Literal["legal-reference-alias-map-1"]
    consolidation_sha256: Digest
    provenance: dict
    aliases: list[AliasAssociation] = Field(max_length=1000)


class MeaningReview(StrictModel):
    finding_id: Nonblank
    original_response_sha256s: list[Digest] = Field(min_length=1)
    status: Literal["DRAFT", "VERIFIED", "REJECTED", "UNCERTAIN"]
    author_alias: Alias | None = None
    reviewer_alias: Alias | None = None
    reviewed_at: datetime | None = None
    review_notes: str = ""

    @model_validator(mode="after")
    def human_review_required(self):
        if self.status != "DRAFT":
            if (
                not self.author_alias
                or not self.reviewer_alias
                or self.author_alias == self.reviewer_alias
                or self.reviewed_at is None
                or self.reviewed_at.tzinfo is None
                or not self.review_notes.strip()
            ):
                raise ValueError("A dated independent human review with notes is required")
        if len(set(self.original_response_sha256s)) != len(self.original_response_sha256s):
            raise ValueError("Duplicate original artifact hashes")
        return self


class MeaningReviews(StrictModel):
    schema_version: Literal["legal-reference-meaning-reviews-1"]
    consolidation_sha256: Digest
    provenance: dict
    reviews: list[MeaningReview] = Field(max_length=1000)
