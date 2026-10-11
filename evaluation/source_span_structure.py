"""Diagnose whether a reviewed span is representable by one production chunk."""

from collections import Counter

from evaluation.external_source_span_metrics import VerifiedSpan, matching_ranks
from tendercite.services.retrieval.base import SearchHit
from tendercite.services.text import normalize_quote

STATUSES = ("complete", "split_across_chunks", "too_long", "mapping_inconsistency")


def diagnose_span(
    span: VerifiedSpan,
    page_text: str | None,
    indexed_chunks: list[SearchHit],
    *,
    chunk_size: int = 1200,
) -> dict:
    located = [
        chunk
        for chunk in indexed_chunks
        if chunk.document_id == span.document_id
        and chunk.document_name == span.source_document
        and chunk.page_number == span.page_number
    ]
    _, ranks = matching_ranks(span, located)
    if page_text is None or span.exact_source_quote not in page_text or not located:
        status = "mapping_inconsistency"
    elif ranks:
        status = "complete"
    elif len(span.exact_source_quote) > chunk_size:
        status = "too_long"
    else:
        status = "split_across_chunks"
    return {
        "span_id": span.span_id,
        "status": status,
        "raw_quote_characters": len(span.exact_source_quote),
        "normalized_quote_characters": len(normalize_quote(span.exact_source_quote)),
        "matching_indexed_chunk_ids": [located[rank - 1].chunk_id for rank in ranks],
    }


def summarize_structure(diagnostics: list[dict]) -> dict:
    counts = Counter(row["status"] for row in diagnostics)
    return {"verified_spans": len(diagnostics), **{status: counts[status] for status in STATUSES}}
