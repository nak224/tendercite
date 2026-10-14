"""Pure source checks and ordinary analysis construction; no inference or review decisions."""

import hashlib
from dataclasses import dataclass

from pydantic import ValidationError

from tendercite.domain.analysis import AnalysisRequest, AnalysisRun
from tendercite.domain.curated_intake import (
    CuratedIntakeError,
    CuratedIntakeProvenance,
    CuratedIntakeResult,
    CuratedTenderIntake,
    IntakeErrorCode,
    IntakeItemIdentity,
    ValidatedIntakePage,
    intake_analysis_id,
    intake_content_hash,
    intake_finding_id,
)
from tendercite.domain.models import EvidenceRef, Finding, GroundingStatus, ReviewStatus
from tendercite.services.evidence import validate_evidence


@dataclass(frozen=True)
class ValidatedIntake:
    provenance: CuratedIntakeProvenance
    evidence: tuple[tuple[EvidenceRef, ...], ...]


def checked_intake(value) -> CuratedTenderIntake:
    try:
        return CuratedTenderIntake.model_validate(value)
    except ValidationError:
        raise CuratedIntakeError(IntakeErrorCode.INVALID_INPUT) from None


def validate_intake_sources(intake, *, documents, pages) -> ValidatedIntake:
    """Inputs must come from one repository transaction; reuse quotation semantics exactly."""
    intake = checked_intake(intake)
    for source in intake.documents:
        document = documents.get(source.document_id)
        if document is None:
            raise CuratedIntakeError(IntakeErrorCode.DOCUMENT_NOT_FOUND)
        if document.media_type != "application/pdf":
            raise CuratedIntakeError(IntakeErrorCode.NOT_PDF)
        if document.sha256 != source.expected_pdf_sha256:
            raise CuratedIntakeError(IntakeErrorCode.HASH_MISMATCH)
    cited = {(e.document_id, e.page_number) for f in intake.findings for e in f.evidence}
    if any(key not in pages for key in cited):
        raise CuratedIntakeError(IntakeErrorCode.PAGE_NOT_FOUND)
    for source in intake.documents:
        document = documents[source.document_id]
        available = sorted(number for doc, number in pages if doc == source.document_id)
        if (
            len(available) != document.page_count
            or not available
            or any(number != index for index, number in enumerate(available, start=1))
        ):
            raise CuratedIntakeError(IntakeErrorCode.PAGES_INCOMPLETE)
    evidence = []
    for candidate in intake.findings:
        refs = []
        for citation in candidate.evidence:
            page = pages[(citation.document_id, citation.page_number)]
            if page.document_id != citation.document_id or page.page_number != citation.page_number:
                raise CuratedIntakeError(IntakeErrorCode.STORAGE_INTEGRITY)
            ref = validate_evidence(
                document_id=citation.document_id,
                page_number=citation.page_number,
                page_text=page.text,
                quote=citation.quote,
            )
            if ref.grounding_status != GroundingStatus.VERIFIED_QUOTE:
                raise CuratedIntakeError(IntakeErrorCode.INVALID_QUOTE)
            refs.append(ref)
        evidence.append(tuple(refs))
    return ValidatedIntake(
        provenance=CuratedIntakeProvenance(
            intake_content_hash=intake_content_hash(intake),
            intake=intake,
            item_identities=tuple(
                IntakeItemIdentity(
                    item_id=f.item_id, finding_id=intake_finding_id(intake, f.item_id)
                )
                for f in intake.findings
            ),
            validated_pages=tuple(
                ValidatedIntakePage(
                    document_id=doc,
                    page_number=number,
                    stored_page_sha256=hashlib.sha256(
                        pages[(doc, number)].text.encode("utf-8")
                    ).hexdigest(),
                )
                for doc, number in sorted(cited)
            ),
        ),
        evidence=tuple(evidence),
    )


def build_curated_analysis(validated: ValidatedIntake, *, created_at) -> AnalysisRun:
    intake = validated.provenance.intake
    analysis_id = intake_analysis_id(intake)
    return AnalysisRun(
        id=analysis_id,
        provider="manual_operator_entry",
        model="not_invoked",
        embedding_model="not_invoked",
        prompt_version="manual-intake-no-prompt-1",
        schema_version=intake.schema_version,
        # Query/top_k are compatibility fields; provenance explicitly records no retrieval.
        request=AnalysisRequest(
            document_ids=[d.document_id for d in intake.documents], query=intake.purpose
        ),
        retrieved_chunk_ids=[],
        created_at=created_at,
        curated_intake=validated.provenance,
        findings=[
            Finding(
                id=intake_finding_id(intake, candidate.item_id),
                analysis_run_id=analysis_id,
                statement=candidate.statement,
                category=candidate.category,
                requirement_type=candidate.requirement_type,
                evidence=list(evidence),
                review_status=ReviewStatus.UNREVIEWED,
                created_at=created_at,
            )
            for candidate, evidence in zip(intake.findings, validated.evidence, strict=True)
        ],
    )


def intake_summary(intake, *, status):
    return CuratedIntakeResult(
        status=status,
        analysis_id=intake_analysis_id(intake),
        intake_content_hash=intake_content_hash(intake),
        source_count=len(intake.documents),
        finding_count=len(intake.findings),
        quotation_count=sum(len(f.evidence) for f in intake.findings),
    )
