"""Exact source-span/type matching; never a measure of semantic statement correctness."""

from tendercite.services.text import normalize_quote


def retrieval_metrics(gold, results, k):
    successes = sum(
        any(hit["page_number"] == case["page"] for hit in results.get(case["id"], [])[:k])
        for case in gold
    )
    return {"queries": len(gold), "k": k, "hit_at_k": successes / len(gold) if gold else None}


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
