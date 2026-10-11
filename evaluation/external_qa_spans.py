"""Separate external QA source-span schema and offline human-review preparation."""

import hashlib
import json
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictInt,
    StrictStr,
    StringConstraints,
    field_validator,
    model_validator,
)

from evaluation import external_benchmark as acquisition
from evaluation.external_document_cases import DATASET_REVISION, FAMILY, prepare_cases
from evaluation.external_qa import safe_name
from tendercite.services.pdf_parser import PyPdfParser

SCHEMA_VERSION = "external-qa-source-spans-1"
Digest = Annotated[StrictStr, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
Revision = Annotated[StrictStr, StringConstraints(pattern=r"^[0-9a-f]{40}$")]
Alias = Annotated[StrictStr, StringConstraints(pattern=r"^[a-z][a-z0-9_-]{0,63}$")]
Text = Annotated[StrictStr, StringConstraints(min_length=1)]
QuestionType = Literal[
    "Single-document lookup",
    "Single-document reasoning",
    "Cross-document factual",
    "Implicit multi-hop",
    "Unanswerable",
]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ReferencePDF(StrictModel):
    filename: Text
    pdf_sha256: Digest

    @field_validator("filename")
    @classmethod
    def pdf_filename(cls, value):
        safe_name(value)
        if not value.lower().endswith(".pdf"):
            raise ValueError("Reference must name a PDF")
        return value


class SourceEvidence(StrictModel):
    source_document: Text
    pdf_sha256: Digest
    page_number: Annotated[StrictInt, Field(ge=1)]
    exact_source_quote: Text
    interpretation_note: StrictStr | None = None
    review_status: Literal["DRAFT", "VERIFIED", "REJECTED"]
    author_alias: Alias
    reviewer_alias: Alias | None = None
    review_notes: StrictStr

    @field_validator("source_document")
    @classmethod
    def safe_filename(cls, value):
        return ReferencePDF.pdf_filename(value)

    @field_validator("exact_source_quote")
    @classmethod
    def nonblank_quote(cls, value):
        if not value.strip():
            raise ValueError("Quote must not be blank")
        return value  # Never trim, normalize or case-fold evidence.

    @model_validator(mode="after")
    def reviewed_metadata(self):
        if self.review_status == "VERIFIED" and (
            self.reviewer_alias is None or self.reviewer_alias == self.author_alias
        ):
            raise ValueError("VERIFIED requires a reviewer distinct from the author")
        if self.review_status != "DRAFT" and not self.review_notes.strip():
            raise ValueError("VERIFIED/REJECTED evidence requires nonblank review notes")
        return self


class QuestionMetadata(StrictModel):
    question_id: Text
    question: Text
    question_type: QuestionType
    source_documents: Annotated[list[ReferencePDF], Field(min_length=1)]
    annotation_notes: StrictStr = ""


class ReviewQuestion(QuestionMetadata):
    evidence: Annotated[list[SourceEvidence], Field(max_length=100)]


class ReviewHeader(StrictModel):
    annotation_schema_version: Literal["external-qa-source-spans-1"]
    source_repository: Literal[acquisition.REPOSITORY]
    dataset_revision: Revision
    family: Text
    acquisition_manifest_sha256: Digest
    eligibility_results_sha256: Digest
    label_origin: Literal["human_source_spans_over_upstream_candidate_qa"]


class ReviewFile(ReviewHeader):
    questions: Annotated[list[ReviewQuestion], Field(max_length=1000)]


def read_local_json(path: Path):
    with acquisition.confined(path).open("rb") as stream:
        raw = stream.read(8 * 1024 * 1024 + 1)
    if len(raw) > 8 * 1024 * 1024:
        raise ValueError("Local review/eligibility JSON exceeds 8 MiB")
    return json.loads(raw), hashlib.sha256(raw).hexdigest()


def write_new_json(path: Path, value: dict):
    path = acquisition.confined(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(value, indent=2, ensure_ascii=False) + "\n")


@dataclass
class ReviewContext:
    acquisition_dir: Path
    header: dict
    questions: dict[str, QuestionMetadata]
    documents: dict[str, dict]
    exclusions: list[dict]


def load_review_context(
    directory: Path, retrieval_results: Path, *, family=FAMILY, revision=DATASET_REVISION
) -> ReviewContext:
    prepared = prepare_cases(directory, family=family, revision=revision)
    results, results_hash = read_local_json(retrieval_results)
    if not isinstance(results, dict):
        raise ValueError("3B-a eligibility results must be a JSON object")
    _, manifest_hash = read_local_json(directory / "manifest.json")
    if (
        results.get("status") != "completed_real_document_retrieval"
        or results.get("family") != family
        or results.get("dataset_revision") != revision
        or results.get("eligible_cases") != len(prepared.eligible)
        or results.get("exclusions") != prepared.excluded
    ):
        raise ValueError("3B-a eligibility results disagree with the pinned acquisition")
    recorded = results.get("cases", [])
    if not isinstance(recorded, list) or len(recorded) != len(prepared.eligible):
        raise ValueError("3B-a results have missing/extra eligible cases")
    rows = {row["id"]: row for row in recorded}
    if len(rows) != len(recorded) or set(rows) != {row.id for row in prepared.eligible}:
        raise ValueError("3B-a results have duplicate or incorrect question IDs")
    hashes = {item["filename"]: item.get("sha256") for item in results["dataset_artifacts"]}
    questions = {}
    for case in prepared.eligible:
        row = rows[case.id]
        if (row["question"], row["question_type"], row["expected_source_documents"]) != (
            case.question,
            case.question_type,
            case.source_documents,
        ):
            raise ValueError("3B-a question/type/reference labels differ from original metadata")
        references = []
        for name in case.source_documents:
            document = prepared.documents[name]
            if hashes.get(document["filename"]) != document["sha256"]:
                raise ValueError("3B-a artifact hashes disagree with the acquisition manifest")
            references.append(ReferencePDF(filename=name, pdf_sha256=document["sha256"]))
        questions[case.id] = QuestionMetadata(
            question_id=case.id,
            question=case.question,
            question_type=case.question_type,
            source_documents=references,
        )
    if not questions:
        raise ValueError("No eligible questions to review")
    header = ReviewHeader(
        annotation_schema_version=SCHEMA_VERSION,
        source_repository=acquisition.REPOSITORY,
        dataset_revision=revision,
        family=family,
        acquisition_manifest_sha256=manifest_hash,
        eligibility_results_sha256=results_hash,
        label_origin="human_source_spans_over_upstream_candidate_qa",
    ).model_dump()
    return ReviewContext(directory, header, questions, prepared.documents, prepared.excluded)


def make_review_template(context: ReviewContext) -> dict:
    return ReviewFile(
        **context.header,
        questions=[
            ReviewQuestion(**q.model_dump(), evidence=[]) for q in context.questions.values()
        ],
    ).model_dump()


def parsed_reference_pages(context: ReviewContext) -> dict[str, dict[int, str]]:
    """Parse verified byte snapshots; preserve exact production parser output."""
    manifest, _ = read_local_json(context.acquisition_dir / "manifest.json")
    inventory = {row["filename"]: row for row in manifest["inventory"]}
    names = sorted({ref.filename for q in context.questions.values() for ref in q.source_documents})
    output = {}
    for name in names:
        document = context.documents[name]
        raw = acquisition.read_artifact(
            document["local_path"], inventory[document["filename"]], document
        )
        with tempfile.TemporaryDirectory(dir=context.acquisition_dir, prefix=".qa-span-") as tmp:
            snapshot = Path(tmp) / "snapshot.pdf"
            snapshot.write_bytes(raw)
            pages = PyPdfParser().parse(snapshot, max_pages=250, max_chars=1_000_000)
        output[name] = {page.page_number: page.text for page in pages}
    return output


def export_pages(context: ReviewContext, directory: Path) -> dict:
    directory = acquisition.confined(directory)
    pages = parsed_reference_pages(context)
    directory.mkdir(parents=True, exist_ok=False)
    index = {**context.header, "documents": []}
    for name, texts in pages.items():
        digest = context.documents[name]["sha256"]
        folder = directory / digest
        folder.mkdir()
        records = []
        for number, text in texts.items():
            path = folder / f"page-{number:04d}.txt"
            path.write_text(text, encoding="utf-8", newline="")
            records.append(
                {
                    "page_number": number,
                    "text_file": str(path.relative_to(directory)),
                    "text_sha256": hashlib.sha256(text.encode()).hexdigest(),
                }
            )
        index["documents"].append({"filename": name, "pdf_sha256": digest, "pages": records})
    write_new_json(directory / "index.json", index)
    return index
