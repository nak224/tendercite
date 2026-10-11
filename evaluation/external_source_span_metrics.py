"""Recorded chunk-rank metrics for reviewed QA evidence, never answer correctness."""

from collections import defaultdict
from dataclasses import dataclass

from tendercite.services.retrieval.base import SearchHit
from tendercite.services.text import normalize_quote

KS = (1, 3, 5, 10, 20, 50)
METRICS = (
    "page_hit_at_k",
    "source_span_hit_at_k",
    "all_source_documents_supported_at_k",
    "verified_span_recall_at_k",
)
DIAGNOSTIC_METRICS = ("source_span_hit_at_k", "verified_span_recall_at_k")


@dataclass(frozen=True)
class VerifiedSpan:
    span_id: str
    source_document: str
    document_id: str
    pdf_sha256: str
    page_number: int
    exact_source_quote: str


def matching_ranks(span: VerifiedSpan, hits: list[SearchHit]) -> tuple[list[int], list[int]]:
    """Case-sensitive normalized complete quote in ONE correctly located chunk."""
    quote = normalize_quote(span.exact_source_quote)
    if not quote:
        raise ValueError("Cannot score an empty source quote")
    page_ranks, span_ranks = [], []
    for rank, hit in enumerate(hits, 1):
        if (
            hit.document_id == span.document_id
            and hit.document_name == span.source_document
            and hit.page_number == span.page_number
        ):
            page_ranks.append(rank)
            if quote in normalize_quote(hit.text):
                span_ranks.append(rank)
    return page_ranks, span_ranks


def case_metrics(
    spans: list[VerifiedSpan],
    required_documents: list[str],
    hits: list[SearchHit],
    ks: tuple[int, ...] = KS,
) -> dict:
    """k counts original chunk positions, including duplicate hits; spans count once."""
    if not ks or len(set(ks)) != len(ks) or any(type(k) is not int or not 1 <= k <= 50 for k in ks):
        raise ValueError("Chunk k must be unique and between 1 and 50")
    if len(hits) > 50:
        raise ValueError("Recorded retrieval exceeds the production top-50 chunk limit")
    if len({span.span_id for span in spans}) != len(spans):
        raise ValueError("Span IDs must be unique")
    required = set(required_documents)
    if not required or {span.document_id for span in spans} != required:
        raise ValueError(
            "Every originally required document must have verified supporting evidence"
        )
    ranks = [matching_ranks(span, hits) for span in spans]
    output = {name: {} for name in METRICS}
    matched_ids = {}
    for k in ks:
        key = str(k)
        matched = [span for span, (_, sr) in zip(spans, ranks, strict=True) if sr and sr[0] <= k]
        output["page_hit_at_k"][key] = any(pr and pr[0] <= k for pr, _ in ranks)
        output["source_span_hit_at_k"][key] = bool(matched)
        output["all_source_documents_supported_at_k"][key] = required.issubset(
            span.document_id for span in matched
        )
        output["verified_span_recall_at_k"][key] = len(matched) / len(spans)
        matched_ids[key] = [span.span_id for span in matched]
    first_ranks = {
        span.span_id: sr[0] if sr else None for span, (_, sr) in zip(spans, ranks, strict=True)
    }
    return {
        **output,
        "verified_span_count": len(spans),
        "first_matching_chunk_rank": min((r for r in first_ranks.values() if r), default=None),
        "first_matching_chunk_rank_by_span": first_ranks,
        "matched_span_ids_at_k": matched_ids,
        "missed_span_ids_at_k": {
            str(k): [span.span_id for span in spans if span.span_id not in matched_ids[str(k)]]
            for k in ks
        },
    }


def scorable_metrics(spans: list[VerifiedSpan], hits: list[SearchHit], ks=KS) -> dict:
    """Conditional diagnostic; no spans means no denominator, rather than a zero score."""
    ranks = [matching_ranks(span, hits)[1] for span in spans]
    return {
        "scorable_span_count": len(spans),
        "source_span_hit_at_k": {
            str(k): any(sr and sr[0] <= k for sr in ranks) if spans else None for k in ks
        },
        "verified_span_recall_at_k": {
            str(k): sum(bool(sr and sr[0] <= k) for sr in ranks) / len(spans) if spans else None
            for k in ks
        },
    }


def aggregate(cases: list[dict], ks=KS) -> dict:
    """Question-macro averages; diagnostic averages exclude questions with no scorable spans."""

    def summarize(rows):
        diagnostic = [
            r["scorable_diagnostic"]
            for r in rows
            if r["scorable_diagnostic"]["scorable_span_count"]
        ]
        return {
            "questions": len(rows),
            "verified_spans": sum(r["strict"]["verified_span_count"] for r in rows),
            "strict": {
                name: {
                    str(k): sum(r["strict"][name][str(k)] for r in rows) / len(rows)
                    if rows
                    else None
                    for k in ks
                }
                for name in METRICS
            },
            "scorable_diagnostic": {
                "questions_with_scorable_spans": len(diagnostic),
                "scorable_spans": sum(r["scorable_span_count"] for r in diagnostic),
                **{
                    name: {
                        str(k): sum(r[name][str(k)] for r in diagnostic) / len(diagnostic)
                        if diagnostic
                        else None
                        for k in ks
                    }
                    for name in DIAGNOSTIC_METRICS
                },
            },
        }

    groups = defaultdict(list)
    for case in cases:
        groups[case["question_type"]].append(case)
    return {
        "combined": summarize(cases),
        "by_question_type": {kind: summarize(rows) for kind, rows in sorted(groups.items())},
    }
