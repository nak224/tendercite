"""Exact source-span/type matching; never a measure of semantic statement correctness."""

from tendercite.services.text import normalize_quote


def retrieval_metrics(gold, results, k, document_id):
    """Separate page discovery from retrieval of the complete normalized gold span.

    Both metrics require the selected document and page. A source-span hit also
    requires the full gold quote in one returned chunk, not elsewhere on the page
    or assembled from multiple chunks. Partial spans count as misses.
    """
    if k < 1:
        raise ValueError("k must be positive")
    page_hits = 0
    span_hits = 0
    for case in gold:
        page_matches = [
            hit
            for hit in results.get(case["id"], [])[:k]
            if hit["document_id"] == document_id and hit["page_number"] == case["page"]
        ]
        quote = normalize_quote(case["quote"])
        page_hits += bool(page_matches)
        span_hits += bool(quote) and any(
            quote in normalize_quote(hit["text"]) for hit in page_matches
        )
    return {
        "queries": len(gold),
        "k": k,
        "page_hit_at_k": page_hits / len(gold) if gold else None,
        "source_span_hit_at_k": span_hits / len(gold) if gold else None,
    }


def extraction_metrics(gold, findings, document_id):
    matched = set()
    evidence = [e for f in findings for e in f["evidence"]]
    supported = sum(
        bool(f["evidence"])
        and all(e["grounding_status"] == "VERIFIED_QUOTE" for e in f["evidence"])
        for f in findings
    )
    # One prediction can match at most one gold requirement; duplicates count against precision.
    for finding in findings:
        for case in gold:
            if case["id"] in matched:
                continue
            if finding["category"] != case["category"]:
                continue
            if finding["requirement_type"] != case["requirement_type"]:
                continue
            if any(
                e["document_id"] == document_id
                and e["page_number"] == case["page"]
                and normalize_quote(e["quote"]) == normalize_quote(case["quote"])
                and e["grounding_status"] == "VERIFIED_QUOTE"
                for e in finding["evidence"]
            ):
                matched.add(case["id"])
                break
    precision = len(matched) / len(findings) if findings else 0.0
    recall = len(matched) / len(gold) if gold else 0.0
    return {
        "gold_count": len(gold),
        "prediction_count": len(findings),
        "matched_count": len(matched),
        "source_span_type_precision": precision,
        "source_span_type_recall": recall,
        "source_span_type_f1": 2 * precision * recall / (precision + recall)
        if precision + recall
        else 0.0,
        "evidence_verification_rate": sum(
            e["grounding_status"] == "VERIFIED_QUOTE" for e in evidence
        )
        / len(evidence)
        if evidence
        else None,
        "unsupported_finding_rate": 1 - supported / len(findings) if findings else None,
    }
