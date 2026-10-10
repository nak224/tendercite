"""Validate upstream QA candidates and document references; never generate human gold."""

import re
from collections import Counter
from pathlib import PurePosixPath
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictInt,
    StrictStr,
    StringConstraints,
    ValidationError,
    field_validator,
    model_validator,
)

QUESTION_TYPES = (
    "Single-document lookup",
    "Single-document reasoning",
    "Cross-document factual",
    "Implicit multi-hop",
    "Unanswerable",
)
Text = Annotated[StrictStr, StringConstraints(min_length=1)]


def safe_name(value: str) -> str:
    """One portable filename component, not a path, URL or Windows device name."""
    if (
        value in {"", ".", ".."}
        or value != value.strip()
        or value.endswith(".")
        or any(ord(char) < 32 or char in '/\\:*?"<>|' for char in value)
        or re.fullmatch(r"(?i)(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?", value)
    ):
        raise ValueError("Unsafe filename/family component")
    return value


def safe_repo_path(value: str) -> str:
    path = PurePosixPath(value)
    if path.is_absolute() or str(path) != value:
        raise ValueError("Unsafe repository path")
    for part in path.parts:
        safe_name(part)
    return value


def chunk_document(chunk_id: str) -> str:
    match = re.fullmatch(r"(.+\.[pP][dD][fF])_([0-9]+)", chunk_id)
    if match is None:
        raise ValueError("Unsupported relevant_chunks identifier; expected filename.pdf_ordinal")
    return safe_name(match[1])  # Ordinals are not interpreted as pages or TenderCite chunk IDs.


class QARecord(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: Text
    family: Text
    question: Text
    answer: Text
    question_type: Literal[
        "Single-document lookup",
        "Single-document reasoning",
        "Cross-document factual",
        "Implicit multi-hop",
        "Unanswerable",
    ]
    type_number: Annotated[StrictInt, Field(ge=1, le=5)]
    hop_count: Annotated[StrictInt, Field(ge=0, le=4)]
    difficulty: Literal["easy", "medium", "hard"]
    relevant_chunks: list[Text]
    source_documents: list[Text]
    documents_in_family: list[Text]
    reasoning_note: Text

    @field_validator("id", "question", "answer", "reasoning_note")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Value must not be blank")
        return value

    @field_validator("family")
    @classmethod
    def safe_family(cls, value: str) -> str:
        return safe_name(value)

    @field_validator("source_documents", "documents_in_family")
    @classmethod
    def pdf_names(cls, values: list[str]) -> list[str]:
        for value in values:
            safe_name(value)
            if not value.lower().endswith(".pdf"):
                raise ValueError("Document references must name PDF files")
        if len(set(values)) != len(values):
            raise ValueError("Duplicate document references")
        return values

    @model_validator(mode="after")
    def consistent_references(self):
        if QUESTION_TYPES[self.type_number - 1] != self.question_type:
            raise ValueError("question_type and type_number contradict each other")
        if not set(self.source_documents) <= set(self.documents_in_family):
            raise ValueError("source_documents contains a document outside documents_in_family")
        for chunk in self.relevant_chunks:
            if chunk_document(chunk) not in self.source_documents:
                raise ValueError("relevant_chunks references a document outside source_documents")
        return self


def validate_metadata(payload, inventory: set[str]) -> tuple[list[QARecord], dict]:
    if not isinstance(payload, list) or len(payload) > 1000:
        raise ValueError("QA metadata must be an array of at most 1000 records")
    valid, failures = [], []
    counts = Counter(
        row["id"] for row in payload if isinstance(row, dict) and isinstance(row.get("id"), str)
    )
    for index, row in enumerate(payload):
        errors = []
        try:
            record = QARecord.model_validate(row)
            if counts[record.id] != 1:
                errors.append("Duplicate QA ID")
            for filename in record.documents_in_family:
                if f"data/{record.family}/{filename}" not in inventory:
                    errors.append(f"Referenced PDF absent from repository: {filename}")
            if record.question_type != "Unanswerable" and not (
                record.source_documents and record.relevant_chunks
            ):
                errors.append("Answerable case has no document/chunk evidence references")
            if not errors:
                valid.append(record)
        except ValidationError as exc:
            errors = [
                f"{'.'.join(map(str, error['loc'])) or 'record'}: {error['msg']}"
                for error in exc.errors(include_input=False, include_url=False)
            ]
        if errors:
            failures.append(
                {
                    "input_index": index,
                    "id": row.get("id") if isinstance(row, dict) else None,
                    "errors": errors,
                }
            )
    summary = {
        "records": len(payload),
        "valid_records": len(valid),
        "invalid_records": failures,
        "family_counts": dict(sorted(Counter(row.family for row in valid).items())),
        "question_types": dict(sorted(Counter(row.question_type for row in valid).items())),
        "unanswerable_ids": [row.id for row in valid if row.question_type == "Unanswerable"],
        "metadata_review_flags": [
            {"id": row.id, "reason": "Unanswerable case has evidence references or nonzero hops"}
            for row in valid
            if row.question_type == "Unanswerable"
            and (row.source_documents or row.relevant_chunks or row.hop_count != 0)
        ],
    }
    return valid, summary


def family_validation(records: list[QARecord], family: str | None, pdfs: dict) -> dict:
    cases = []
    for record in records:
        if record.family != family:
            continue
        usable = all(
            pdfs.get(f"data/{family}/{filename}", {}).get("status") == "passed"
            for filename in record.source_documents
        ) and bool(record.source_documents)
        unanswerable = record.question_type == "Unanswerable"
        cases.append(
            {
                "id": record.id,
                "question_type": record.question_type,
                "source_documents": record.source_documents,
                "document_references_usable": usable and not unanswerable,
                "unanswerable": unanswerable,
                "chunk_references": [
                    {
                        "upstream_chunk_id": chunk,
                        "repository_file": f"data/{family}/{chunk_document(chunk)}",
                        "original_chunk_reconstructed": False,
                        "page_number": None,
                        "source_span": None,
                    }
                    for chunk in record.relevant_chunks
                ],
                "human_review_required": True,
                "review_flags": [
                    "upstream_answers_not_independent_human_gold",
                    "original_chunk_boundaries_unavailable",
                ]
                + ([] if usable or unanswerable else ["source_documents_not_parsed"]),
            }
        )
    return {
        "family": family,
        "questions": len(cases),
        "answerable": sum(not case["unanswerable"] for case in cases),
        "unanswerable": sum(case["unanswerable"] for case in cases),
        "usable_document_reference_questions": sum(
            case["document_references_usable"] for case in cases
        ),
        "exact_source_span_ground_truth_questions": 0,
        "independently_human_verified_questions": 0,
        "pdfs": pdfs,
        "cases": cases,
        "retrieval_benchmark_ready": False,
    }
