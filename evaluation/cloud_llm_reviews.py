"""Optional human verdicts and independently revalidated source spans, offline only."""

from datetime import datetime
from typing import Literal

from pydantic import Field, field_validator

from evaluation.cloud_llm_contexts import StrictModel
from evaluation.external_qa_spans import Alias, Digest, load_review_context, read_local_json
from evaluation.validate_external_qa_spans import validate_review


class HumanVerdict(StrictModel):
    attempt_id: str = Field(min_length=1)
    question_id: str = Field(min_length=1)
    strategy: Literal["e5_rag", "full_context", "staged_extraction", "staged_synthesis"]
    response_file_sha256: Digest
    reviewer_alias: Alias
    reviewed_by_human: Literal[True]
    reviewed_at: datetime
    correctness: Literal["CORRECT", "INCORRECT", "UNCERTAIN", "NOT_ASSESSED"]
    completeness: Literal["COMPLETE", "PARTIAL", "INCOMPLETE", "UNCERTAIN", "NOT_ASSESSED"]
    notes: str

    @field_validator("reviewed_at")
    @classmethod
    def timezone_required(cls, value):
        if value.tzinfo is None:
            raise ValueError("Human review timestamp must include a timezone")
        return value


class HumanReviews(StrictModel):
    schema_version: Literal["cloud-qa-human-reviews-1"]
    provenance: dict
    reviews: list[HumanVerdict] = Field(max_length=1000)


def load_human_reviews(path, corpus):
    if path is None:
        return {}, None
    raw, digest = read_local_json(path)
    reviews = HumanReviews.model_validate(raw)
    if reviews.provenance != corpus.provenance:
        raise ValueError("Human review provenance differs from the evaluation corpus")
    output = {}
    for review in reviews.reviews:
        key = (review.attempt_id, review.question_id, review.strategy)
        if key in output or review.question_id not in corpus.questions:
            raise ValueError("Duplicate human review or unknown question")
        output[key] = review
    return output, digest


def reviewed_spans(annotations, validation, acquisition_dir, retrieval_results, corpus):
    if annotations is None and validation is None:
        return {}, {"status": "NOT_ASSESSED", "reason": "No independently reviewed spans supplied"}
    if annotations is None or validation is None:
        raise ValueError("Source annotations require their saved validation report")
    payload, digest = read_local_json(annotations)
    saved, validation_hash = read_local_json(validation)
    context = load_review_context(
        acquisition_dir,
        retrieval_results,
        family=corpus.provenance["family"],
        revision=corpus.provenance["dataset_revision"],
    )
    fresh = validate_review(payload, context)
    if saved != {**fresh, "annotations_sha256": digest}:
        raise ValueError("Source-span validation is stale or differs from fresh provenance checks")
    # Partial annotation cohorts are deliberately NOT treated as independent complete gold.
    if fresh["status"] != "COMPLETE":
        return {}, {
            "status": "NOT_ASSESSED",
            "reason": "Independent source-span review is not COMPLETE",
        }
    return {row["question_id"]: row["verified_source_spans"] for row in fresh["questions"]}, {
        "status": "INDEPENDENTLY_REVIEWED_SOURCE_LOCATIONS",
        "annotations_sha256": digest,
        "validation_sha256": validation_hash,
        "semantic_accuracy": "NOT_ASSESSED",
    }
