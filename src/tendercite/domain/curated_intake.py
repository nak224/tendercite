"""Manual candidate requirements, distinct from model output and evaluation gold."""

import hashlib
import json
from enum import StrEnum
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from tendercite.domain.models import RequirementCategory, RequirementType

MAX_INTAKE_BYTES = 1024 * 1024
Identifier = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")]
Digest = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
ContentHash = Annotated[str, StringConstraints(pattern=r"^sha256:[0-9a-f]{64}$")]
Alias = Annotated[str, StringConstraints(pattern=r"^[a-zA-Z0-9_-]{1,64}$")]
Statement = Annotated[str, StringConstraints(min_length=1, max_length=4000, pattern=r"\S")]
Note = Annotated[str, StringConstraints(min_length=1, max_length=1000, pattern=r"\S")]


class IntakeModel(BaseModel):
    model_config = ConfigDict(
        strict=True,
        extra="forbid",
        frozen=True,
        hide_input_in_errors=True,
        revalidate_instances="always",
    )


class IntakeSource(IntakeModel):
    document_id: Identifier
    expected_pdf_sha256: Digest
    source_note: Note | None = None


class SourceQuotation(IntakeModel):
    document_id: Identifier
    page_number: Annotated[int, Field(ge=1)]
    quote: Statement


class ManualRequirement(IntakeModel):
    item_id: Identifier
    statement: Statement
    category: RequirementCategory
    requirement_type: RequirementType
    evidence: Annotated[tuple[SourceQuotation, ...], Field(min_length=1, max_length=20)]
    interpretation_notes: Note | None = None

    @model_validator(mode="after")
    def ordered_evidence(self) -> Self:
        object.__setattr__(
            self,
            "evidence",
            tuple(sorted(self.evidence, key=lambda e: (e.document_id, e.page_number, e.quote))),
        )
        return self


def canonical_intake_content(model: BaseModel) -> bytes:
    return json.dumps(
        model.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


class CuratedTenderIntake(IntakeModel):
    schema_version: Literal["curated-tender-intake-1"]
    source_origin: Literal["manual_operator_entry"]
    operator_alias: Alias | None = None
    purpose: Annotated[str, StringConstraints(min_length=1, max_length=2000, pattern=r"\S")]
    documents: Annotated[tuple[IntakeSource, ...], Field(min_length=1, max_length=100)]
    findings: Annotated[tuple[ManualRequirement, ...], Field(min_length=1, max_length=100)]

    @model_validator(mode="after")
    def consistent_inventory(self) -> Self:
        documents = {d.document_id for d in self.documents}
        if len(documents) != len(self.documents):
            raise ValueError("Duplicate source document IDs")
        if len({f.item_id for f in self.findings}) != len(self.findings):
            raise ValueError("Duplicate local item IDs")
        referenced = {e.document_id for f in self.findings for e in f.evidence}
        if referenced != documents:
            raise ValueError("Declared and referenced document sets must match")
        object.__setattr__(
            self, "documents", tuple(sorted(self.documents, key=lambda d: d.document_id))
        )
        object.__setattr__(self, "findings", tuple(sorted(self.findings, key=lambda f: f.item_id)))
        if len(canonical_intake_content(self)) > MAX_INTAKE_BYTES:
            raise ValueError("Intake exceeds content limit")
        return self


def intake_content_hash(intake: CuratedTenderIntake) -> str:
    return "sha256:" + hashlib.sha256(canonical_intake_content(intake)).hexdigest()


def intake_analysis_id(intake: CuratedTenderIntake) -> str:
    return "curated-" + intake_content_hash(intake).removeprefix("sha256:")


def intake_finding_id(intake: CuratedTenderIntake, item_id: str) -> str:
    raw = json.dumps([intake_analysis_id(intake), item_id], separators=(",", ":")).encode()
    return "curated-finding-" + hashlib.sha256(raw).hexdigest()


class IntakeItemIdentity(IntakeModel):
    item_id: Identifier
    finding_id: Identifier


class ValidatedIntakePage(IntakeModel):
    document_id: Identifier
    page_number: Annotated[int, Field(ge=1)]
    stored_page_sha256: Digest


class CuratedIntakeProvenance(IntakeModel):
    schema_version: Literal["curated-tender-intake-provenance-1"] = (
        "curated-tender-intake-provenance-1"
    )
    intake_content_hash: ContentHash
    intake: CuratedTenderIntake
    item_identities: Annotated[tuple[IntakeItemIdentity, ...], Field(min_length=1, max_length=100)]
    validated_pages: Annotated[
        tuple[ValidatedIntakePage, ...], Field(min_length=1, max_length=2000)
    ]
    provider_invoked: Literal[False] = False
    embeddings_invoked: Literal[False] = False
    retrieval_invoked: Literal[False] = False
    initial_review_status: Literal["UNREVIEWED"] = "UNREVIEWED"

    @model_validator(mode="after")
    def provenance_integrity(self) -> Self:
        if self.intake_content_hash != intake_content_hash(self.intake):
            raise ValueError("Intake content hash mismatch")
        expected = tuple(
            (f.item_id, intake_finding_id(self.intake, f.item_id)) for f in self.intake.findings
        )
        if tuple((i.item_id, i.finding_id) for i in self.item_identities) != expected:
            raise ValueError("Item identity mismatch")
        pages = tuple((p.document_id, p.page_number) for p in self.validated_pages)
        cited = {(e.document_id, e.page_number) for f in self.intake.findings for e in f.evidence}
        if pages != tuple(sorted(cited)):
            raise ValueError("Validated page inventory mismatch")
        return self


class CuratedIntakeResult(IntakeModel):
    schema_version: Literal["curated-tender-intake-result-1"] = "curated-tender-intake-result-1"
    status: Literal["VALIDATED", "CREATED", "ALREADY_PRESENT", "INSPECTED"]
    analysis_id: Identifier
    intake_content_hash: ContentHash
    source_origin: Literal["manual_operator_entry"] = "manual_operator_entry"
    intake_schema_version: Literal["curated-tender-intake-1"] = "curated-tender-intake-1"
    source_count: Annotated[int, Field(ge=1, le=100)]
    finding_count: Annotated[int, Field(ge=1, le=100)]
    quotation_count: Annotated[int, Field(ge=1, le=2000)]


class IntakeErrorCode(StrEnum):
    INVALID_INPUT = "INVALID_CURATED_INTAKE"
    CONFIRMATION_REQUIRED = "MANUAL_INTAKE_CONFIRMATION_REQUIRED"
    DOCUMENT_NOT_FOUND = "SOURCE_DOCUMENT_NOT_FOUND"
    NOT_PDF = "SOURCE_DOCUMENT_NOT_PDF"
    HASH_MISMATCH = "SOURCE_PDF_HASH_MISMATCH"
    PAGES_INCOMPLETE = "SOURCE_PAGES_INCOMPLETE"
    PAGE_NOT_FOUND = "SOURCE_PAGE_NOT_FOUND"
    INVALID_QUOTE = "SOURCE_QUOTE_NOT_VERIFIED"
    IDENTITY_CONFLICT = "INTAKE_IDENTITY_CONFLICT"
    SOURCE_CHANGED = "INTAKE_SOURCE_STATE_CHANGED"
    ANALYSIS_NOT_FOUND = "ANALYSIS_NOT_FOUND"
    NOT_MANUAL = "NOT_A_CURATED_INTAKE"
    STORAGE_INTEGRITY = "INTAKE_STORAGE_INTEGRITY"
    DATABASE_FAILED = "DATABASE_FAILED"


class CuratedIntakeError(ValueError):
    def __init__(self, code: IntakeErrorCode):
        self.code = code
        super().__init__(code.value)
