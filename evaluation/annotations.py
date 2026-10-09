"""Human-authored evaluation annotations; independent of ingestion and model evaluation."""

import hashlib
import json
import tempfile
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Annotated, Literal

from pydantic import (
    AwareDatetime,
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

from evaluation.local_corpus import blob_path, load_corpus, read_pdf_bytes, verify_corpus
from tendercite.domain.models import RequirementCategory, RequirementType
from tendercite.services.pdf_parser import PdfParseError, PyPdfParser

ANNOTATION_SCHEMA_VERSION = "real-tender-annotations-1"
GOLD_DATASET_VERSION = "real-tender-gold-1"
SPLIT_SEED = 224
Alias = Annotated[StrictStr, StringConstraints(pattern=r"^[a-z][a-z0-9_-]{0,63}$")]
Digest = Annotated[StrictStr, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
Nonblank = Annotated[StrictStr, StringConstraints(min_length=1)]


class AnnotationHeader(BaseModel):
    model_config = ConfigDict(extra="forbid")
    annotation_schema_version: Literal["real-tender-annotations-1"]
    corpus_version: Nonblank
    label_origin: Literal["curated_human"]

    @field_validator("corpus_version")
    @classmethod
    def nonblank_version(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("corpus_version must not be blank")
        return value


class HumanAnnotation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    annotation_id: Annotated[StrictStr, StringConstraints(pattern=r"^[a-z0-9][a-z0-9._:-]{0,127}$")]
    tender_id: Nonblank
    document_id: Annotated[StrictStr, StringConstraints(pattern=r"^sha256:[0-9a-f]{64}$")]
    sha256: Digest
    page_number: Annotated[StrictInt, Field(ge=1)]
    source_quote: Nonblank
    requirement_statement: Nonblank
    category: RequirementCategory
    requirement_type: RequirementType
    review_status: Literal["DRAFT", "VERIFIED", "REJECTED"]
    author_alias: Alias
    reviewer_alias: Alias | None = None
    review_notes: StrictStr
    annotated_at: AwareDatetime

    @field_validator("tender_id", "source_quote", "requirement_statement")
    @classmethod
    def nonblank_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Value must not be blank")
        return value  # Quotes are never trimmed, case-folded or whitespace-normalized.

    @field_validator("annotated_at", mode="before")
    @classmethod
    def iso_timestamp(cls, value):
        if not isinstance(value, str):
            raise ValueError("annotated_at must be an ISO-8601 string with a timezone")
        try:
            datetime.fromisoformat(value)
        except ValueError as exc:
            raise ValueError("annotated_at must be an ISO-8601 string with a timezone") from exc
        return value

    @model_validator(mode="after")
    def independent_reviewer(self):
        if self.review_status == "VERIFIED" and (
            self.reviewer_alias is None or self.reviewer_alias == self.author_alias
        ):
            raise ValueError("VERIFIED annotations require a reviewer distinct from the author")
        return self


class AnnotationFile(AnnotationHeader):
    """Typed format for authors and JSON Schema; validation reports each entry separately."""

    annotations: list[HumanAnnotation]


@dataclass(frozen=True)
class AnnotationValidation:
    report: dict
    gold: dict


def tender_split(tender_id: str, seed: int = SPLIT_SEED) -> str:
    """Stable independent assignment; adding annotations/tenders never reshuffles others."""
    digest = hashlib.sha256(f"{seed}\0{tender_id}".encode()).digest()
    bucket = int.from_bytes(digest[:8], "big") % 100
    if bucket < 70:
        return "train"
    if bucket < 85:
        return "development"
    return "held_out_evaluation"


def schema_errors(exc: ValidationError) -> list[str]:
    return [
        f"{'.'.join(map(str, error['loc'])) or 'annotation'}: {error['msg']}"
        for error in exc.errors(include_input=False, include_url=False)
    ]


def source_identity(entry) -> tuple | None:
    # Consolidate labels for the same evidence/classification, even if interpretations differ.
    if not isinstance(entry, dict):
        return None
    fields = ("tender_id", "document_id", "source_quote", "category", "requirement_type")
    values = tuple(entry.get(field) for field in fields)
    page = entry.get("page_number")
    if not all(isinstance(value, str) for value in values) or type(page) is not int:
        return None
    return (*values, page)


def parse_snapshot(directory: Path, document: dict):
    """Parse the exact immutable snapshot whose bytes match the registered hash."""
    content = read_pdf_bytes(blob_path(directory, document["sha256"]))
    if (
        hashlib.sha256(content).hexdigest() != document["sha256"]
        or len(content) != document["bytes"]
    ):
        raise ValueError("SHA-256/size mismatch; stored PDF was changed")
    with tempfile.TemporaryDirectory(prefix="tendercite-annotation-") as temporary:
        path = Path(temporary) / "snapshot.pdf"
        path.write_bytes(content)
        pages = PyPdfParser().parse(path, max_pages=250, max_chars=1_000_000)
    if len(pages) != document.get("page_count"):
        raise ValueError("Extracted page count differs from corpus manifest")
    return pages


def validate_annotations(
    payload: dict, directory: Path, *, seed: int = SPLIT_SEED, source_sha256: str | None = None
) -> AnnotationValidation:
    """Keep per-entry failures and export only occurrence-valid, independently reviewed gold."""
    if not isinstance(payload, dict) or not isinstance(payload.get("annotations"), list):
        raise ValueError("Annotation file must be a JSON object with an annotations array")
    try:
        header = AnnotationHeader.model_validate(
            {key: value for key, value in payload.items() if key != "annotations"}
        )
    except ValidationError as exc:
        raise ValueError("Invalid annotation header: " + "; ".join(schema_errors(exc))) from exc
    entries = payload["annotations"]
    records, models = [], []
    for index, entry in enumerate(entries):
        record = {
            "input_index": index,
            "annotation_id": entry.get("annotation_id") if isinstance(entry, dict) else None,
            "review_status": entry.get("review_status") if isinstance(entry, dict) else None,
            "validation_errors": [],
        }
        try:
            model = HumanAnnotation.model_validate(entry)
        except ValidationError as exc:
            record["validation_errors"] = schema_errors(exc)
            model = None
        records.append(record)
        models.append(model)

    id_counts = Counter(
        entry["annotation_id"]
        for entry in entries
        if isinstance(entry, dict) and isinstance(entry.get("annotation_id"), str)
    )
    identities = [source_identity(entry) for entry in entries]
    identity_counts = Counter(identity for identity in identities if identity is not None)
    corpus_errors, global_errors, document_errors, documents = [], [], {}, {}
    corpus = {"tenders": {}}
    try:
        if not (directory / "manifest.json").is_file():
            raise ValueError("No corpus manifest exists")
        corpus = load_corpus(directory, header.corpus_version)
        if not isinstance(corpus.get("tenders"), dict):
            raise ValueError("Malformed corpus tender associations")
        for index, document in enumerate(corpus["documents"]):
            if not isinstance(document, dict) or not isinstance(document.get("document_id"), str):
                global_errors.append(f"Malformed corpus document at index {index}")
                continue
            doc_id = document["document_id"]
            errors = verify_corpus(directory, {"documents": [document]})
            if doc_id in documents:
                errors.append(f"Duplicate corpus document ID: {doc_id}")
            if not isinstance(document.get("sources"), list) or not isinstance(
                document.get("validation"), dict
            ):
                errors.append(f"Malformed sources/validation for corpus document: {doc_id}")
            elif document["validation"].get("status") != "passed":
                errors.append(f"Corpus document did not pass PDF validation: {doc_id}")
            documents[doc_id] = document
            document_errors.setdefault(doc_id, []).extend(errors)
            corpus_errors.extend(errors)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        global_errors.append(f"Corpus unavailable: {exc}")
    corpus_errors.extend(global_errors)

    pages_by_document, ready = {}, []
    for record, model, identity in zip(records, models, identities, strict=True):
        errors = record["validation_errors"]
        annotation_id = record["annotation_id"]
        if isinstance(annotation_id, str) and id_counts[annotation_id] > 1:
            errors.append("Duplicate annotation ID; all occurrences excluded")
        if identity is not None and identity_counts[identity] > 1:
            errors.append("Duplicate annotation source/category/type; all occurrences excluded")
        if model is not None:
            errors.extend(global_errors)
            document = documents.get(model.document_id)
            if document is None:
                errors.append("Document does not exist in the corpus manifest")
            else:
                errors.extend(document_errors[model.document_id])
                if model.sha256 != document.get("sha256"):
                    errors.append("Annotation SHA-256 does not match the corpus document")
                associated_ids = corpus["tenders"].get(model.tender_id, [])
                sources = document.get("sources", [])
                if (
                    not isinstance(associated_ids, list)
                    or model.document_id not in associated_ids
                    or not isinstance(sources, list)
                    or not any(
                        isinstance(source, dict) and source.get("tender_id") == model.tender_id
                        for source in sources
                    )
                ):
                    errors.append("Tender/document association does not exist in the corpus")
                if not global_errors and not document_errors[model.document_id]:
                    if model.document_id not in pages_by_document:
                        try:
                            pages_by_document[model.document_id] = parse_snapshot(
                                directory, document
                            )
                        except (OSError, ValueError, KeyError, TypeError, PdfParseError) as exc:
                            failure = f"PDF validation failed: {exc}"
                            document_errors[model.document_id].append(failure)
                            corpus_errors.append(f"{model.document_id}: {failure}")
                            errors.append(failure)
                    if pages := pages_by_document.get(model.document_id):
                        if model.page_number > len(pages):
                            errors.append("Page number is outside the extracted PDF page range")
                        elif model.source_quote not in pages[model.page_number - 1].text:
                            errors.append("Exact source quote does not occur on the extracted page")
        record["validation_status"] = "INVALID" if errors else "VALID"
        record["evaluation_ready"] = (
            model is not None and not errors and model.review_status == "VERIFIED"
        )
        record["exclusion_reasons"] = errors.copy()
        if model is not None:
            record["review_notes"] = model.review_notes
            if model.review_status != "VERIFIED":
                record["exclusion_reasons"].append(
                    f"Human review status is {model.review_status}; "
                    "only VERIFIED labels are exported"
                )
        if record["evaluation_ready"]:
            ready.append(
                {**model.model_dump(mode="json"), "split": tender_split(model.tender_id, seed)}
            )

    ready.sort(key=lambda annotation: annotation["annotation_id"])
    summary = {
        "total": len(records),
        "valid": sum(record["validation_status"] == "VALID" for record in records),
        "invalid": sum(record["validation_status"] == "INVALID" for record in records),
        "evaluation_ready": len(ready),
        "excluded": len(records) - len(ready),
        "draft": sum(record["review_status"] == "DRAFT" for record in records),
        "rejected": sum(record["review_status"] == "REJECTED" for record in records),
        "duplicate_annotations": sum(
            any(error.startswith("Duplicate annotation") for error in record["validation_errors"])
            for record in records
        ),
    }
    metadata = {
        **header.model_dump(),
        "annotations_sha256": source_sha256
        or hashlib.sha256(
            json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
        ).hexdigest(),
        "validation_summary": summary,
        "corpus_errors": sorted(set(corpus_errors)),
    }
    report = {**metadata, "results": records}
    gold = {
        **metadata,
        "dataset_version": GOLD_DATASET_VERSION,
        "split_config": {
            "strategy": "sha256-tender-v1",
            "seed": seed,
            "percentages": {"train": 70, "development": 15, "held_out_evaluation": 15},
        },
        "tender_splits": {
            tender_id: tender_split(tender_id, seed)
            for tender_id in sorted({annotation["tender_id"] for annotation in ready})
        },
        "document_hashes": {
            annotation["document_id"]: annotation["sha256"] for annotation in ready
        },
        "annotations": ready,
        "exclusions": [record for record in records if not record["evaluation_ready"]],
    }
    return AnnotationValidation(report, gold)
