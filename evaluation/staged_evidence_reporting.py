"""Offline staged efficiency/coverage alongside historical pair attempts, never answer accuracy."""

from decimal import Decimal
from pathlib import Path

from evaluation.cloud_llm_ledger import amount
from evaluation.staged_evidence_consolidation import consolidate, prepared_synthesis
from evaluation.staged_evidence_execution import extraction_attempts, recorded_step
from evaluation.staged_evidence_plan import synthesis_preflight


def uninspected_staged_row(ledger, entry):
    metadata = entry["staged_step"]
    return {
        "attempt_id": ledger.attempt_id(entry),
        "reservation_key": entry["key"],
        "question_id": entry["question_id"],
        "strategy": "staged_" + metadata["stage"],
        "ledger_strategy": entry["strategy"],
        "plan_id": metadata["plan_id"],
        "batch_id": metadata["batch_id"],
        "step_id": metadata["step_id"],
        "original_reserved_usd": entry["reserved_cost"],
        "historical_ledger_status": entry["status"],
        "effective_ledger_status": ledger.effective_status(entry),
        "usage_derived_cost_usd": entry.get("usage_derived_cost")
        if entry["status"] in ("SETTLED", "OVER_ESTIMATE")
        else None,
        "committed_cost_or_reservation_usd": str(ledger.entry_cost(entry)),
        "response_file": str(Path(entry["output_dir"]) / f"{entry['strategy']}.json"),
        "generation_status": "STAGED_ARTIFACT_NOT_INSPECTED",
        "response_validity": "INVALID_OR_INCOMPLETE",
        "evidence_count": 0,
        "evidence_count_status": "NOT_AVAILABLE",
        "citation_status": "NOT_ASSESSED",
        "semantic_accuracy": "NOT_ASSESSED",
        "human_review": {"status": "NOT_ASSESSED"},
    }


def stage_row(ledger, attempt, corpus, human_reviews, used_reviews):
    entry, record = attempt["entry"], attempt["record"]
    row = uninspected_staged_row(ledger, entry)
    row.update(
        question_type=corpus.questions[entry["question_id"]].question_type,
        generation_status=record.get("generation_status", "MISSING_RESPONSE_ARTIFACT"),
        response_validity=record.get("response_validity", "INVALID_OR_INCOMPLETE"),
        accounting_status=record.get("accounting_status", "UNCERTAIN"),
        completion_status=record.get("completion_status"),
        configured_output_limit=record.get("configured_output_limit"),
        actual_model=record.get("actual_model"),
        provider_request_id=record.get("provider_request_id"),
        http_status=record.get("http_status"),
        latency_seconds=record.get("latency_seconds"),
        completed=attempt["completed"],
        artifact_error=attempt.get("error"),
        response_file_sha256=attempt.get("response_file_sha256"),
        request_payload_sha256=entry["staged_step"]["request_payload_sha256"],
        prompt_version=record.get("prompt_version"),
        response_schema_version=record.get("response_schema_version"),
        experimental_method="NEW_STAGED_PROMPTS_NOT_IDENTICAL_FULL_CONTEXT_PROMPT",
    )
    accounting = record.get("accounting", {})
    for out, key in (
        ("input_tokens", "prompt_tokens"),
        ("output_tokens", "completion_tokens"),
        ("reasoning_tokens", "reasoning_tokens"),
        ("cached_input_tokens", "cached_input_tokens"),
        ("cache_write_tokens", "cache_write_tokens"),
        ("total_tokens", "total_tokens"),
    ):
        row[out] = accounting.get(key)
    validation = record.get("citation_validation", {})
    citations = validation.get("citations", [])
    row["citation_status"] = validation.get("citation_status", "NOT_ASSESSED")
    row["evidence_count"] = len(citations)
    row["evidence_count_status"] = (
        "VALIDATED_STRUCTURED_RECORDS" if record.get("structured_answer") else "NOT_AVAILABLE"
    )
    row["source_references"] = [
        {k: c["citation"][k] for k in ("document_id", "page_number")} for c in citations
    ]
    row["quote_failures"] = [c["failure_reason"] for c in citations if c["failure_reason"]]
    key = (row["attempt_id"], row["question_id"], row["strategy"])
    if review := human_reviews.get(key):
        if review.response_file_sha256 != row["response_file_sha256"] or attempt.get("error"):
            raise ValueError(
                "Human staged verdict is bound to a different/invalid response artifact"
            )
        row["human_review"] = {"status": "HUMAN_DECLARED_VERDICT", **review.model_dump(mode="json")}
        used_reviews.add(key)
    return row


def stage_totals(rows):
    known_cost = sum(
        (
            amount(r["usage_derived_cost_usd"])
            for r in rows
            if r["usage_derived_cost_usd"] is not None
        ),
        Decimal("0"),
    )
    result = {
        "attempted_requests": len(rows),
        "known_usage_derived_cost_usd": str(known_cost),
        "accounting_complete": all(r["usage_derived_cost_usd"] is not None for r in rows),
        "usage_derived_cost_usd": str(known_cost)
        if all(r["usage_derived_cost_usd"] is not None for r in rows)
        else None,
        "known_request_latency_seconds": sum(r.get("latency_seconds") or 0 for r in rows),
        "requests_missing_latency": sum(r.get("latency_seconds") is None for r in rows),
    }
    for field in (
        "input_tokens",
        "output_tokens",
        "reasoning_tokens",
        "cached_input_tokens",
        "cache_write_tokens",
    ):
        counts = [r.get(field) for r in rows]
        result[field] = sum(counts) if all(c is not None for c in counts) else None
        result["known_" + field] = sum(c for c in counts if c is not None)
    return result


def gold_coverage(spans, manifest, steps, attempts, consolidated, corpus):
    if not spans:
        return {
            "status": "NOT_ASSESSED",
            "reason": "No COMPLETE independently reviewed source spans",
        }
    qspans = spans.get(manifest["question_id"], [])
    if not qspans:
        return {"status": "NOT_ASSESSED", "reason": "Selected question has no verified spans"}
    raw_quotes = [
        c
        for attempt in attempts.values()
        if attempt["completed"]
        for finding in attempt["record"]["structured_answer"]["findings"]
        for c in finding["evidence"]
    ]
    final_quotes = [
        e["citation"] for finding in consolidated["findings"] for e in finding["evidence"]
    ]
    rows = []
    for index, span in enumerate(qspans):
        doc_id, number, quote = (
            corpus.mapping[span["source_document"]],
            span["page_number"],
            span["exact_source_quote"],
        )

        def represented(quotes, doc_id=doc_id, number=number, quote=quote):
            return any(
                (c["document_id"], c["page_number"]) == (doc_id, number) and quote in c["quote"]
                for c in quotes
            )

        planned = any(
            p["document_id"] == doc_id and p["page_number"] == number for p in manifest["pages"]
        )
        rows.append(
            {
                "span_index": index,
                "document_name": span["source_document"],
                "document_id": doc_id,
                "page_number": number,
                "in_original_source_coverage": planned and quote in corpus.pages[doc_id][number],
                "in_extracted_evidence": represented(raw_quotes),
                "in_consolidated_evidence": represented(final_quotes),
            }
        )
    count = len(rows)
    return {
        "status": "REVIEWED_SOURCE_SPAN_OCCURRENCE_ONLY",
        "annotated_evidence_spans": count,
        "spans_in_original_source_coverage": sum(r["in_original_source_coverage"] for r in rows),
        "spans_in_extracted_evidence": sum(r["in_extracted_evidence"] for r in rows),
        "spans_in_consolidated_evidence": sum(r["in_consolidated_evidence"] for r in rows),
        "original_source_span_coverage": sum(r["in_original_source_coverage"] for r in rows)
        / count,
        "extracted_complete_quote_recall": sum(r["in_extracted_evidence"] for r in rows) / count,
        "consolidated_complete_quote_recall": sum(r["in_consolidated_evidence"] for r in rows)
        / count,
        "missing_spans": [r for r in rows if not r["in_consolidated_evidence"]],
        "missing_documents": sorted(
            {r["document_name"] for r in rows if not r["in_consolidated_evidence"]}
        ),
        "spans": rows,
        "semantic_correctness": "NOT_ASSESSED",
        "definition": (
            "A complete reviewed quote must occur verbatim within accepted extracted evidence "
            "on the same document/page; no partial-overlap or semantic scoring."
        ),
    }


def staged_report(ledger, loaded, corpus, *, spans=None, human_reviews=None, used_reviews=None):
    manifest, config, settings, steps, _, file_hash = loaded
    attempts = extraction_attempts(ledger, manifest, steps, config, settings, corpus)
    consolidated = consolidate(manifest, attempts)
    reviews, used = human_reviews or {}, used_reviews if used_reviews is not None else set()
    rows = [stage_row(ledger, attempt, corpus, reviews, used) for attempt in attempts.values()]
    synthesis_entries = [
        e
        for e in ledger.data["entries"]
        if e.get("staged_step", {}).get("plan_id") == manifest["plan_id"]
        and e["staged_step"]["stage"] == "synthesis"
    ]
    for entry in synthesis_entries:
        if consolidated["status"] == "COMPLETE":
            step = prepared_synthesis(manifest, consolidated, corpus)
            effective, report, _ = synthesis_preflight(config, settings, corpus, step)
            attempt = recorded_step(
                ledger, entry, step, report["request_preflight"][0], effective, corpus, manifest
            )
            rows.append(stage_row(ledger, attempt, corpus, reviews, used))
        else:
            row = uninspected_staged_row(ledger, entry)
            row["artifact_error"] = (
                "Synthesis cannot be revalidated because extraction artifacts are incomplete"
            )
            rows.append(row)
    extraction_rows = [r for r in rows if r["strategy"] == "staged_extraction"]
    synthesis_rows = [r for r in rows if r["strategy"] == "staged_synthesis"]
    totals = stage_totals(rows)
    known_spend = amount(totals["known_usage_derived_cost_usd"])
    extraction_totals = stage_totals(extraction_rows)
    extraction_cost = amount(extraction_totals["known_usage_derived_cost_usd"])
    synthesis_valid = any(r.get("completed") for r in synthesis_rows)
    pipeline_status = (
        "EXTRACTION_INCOMPLETE"
        if consolidated["status"] != "COMPLETE"
        else "COMPLETED_WITH_VALIDATED_SYNTHESIS"
        if synthesis_valid
        else "SYNTHESIS_INCOMPLETE"
        if synthesis_rows
        else "EXTRACTION_COMPLETE_SYNTHESIS_NOT_EXECUTED"
    )
    findings = consolidated["accepted_raw_finding_count"]
    evidence_sources = sorted(
        {
            (c["document_id"], c["page_number"])
            for f in consolidated["findings"]
            for e in f["evidence"]
            for c in [e["citation"]]
        }
    )
    return {
        "schema_version": "staged-evidence-report-1",
        "plan_id": manifest["plan_id"],
        "plan_file_sha256": file_hash,
        "question_id": manifest["question_id"],
        "pipeline_status": pipeline_status,
        "planned_optional_synthesis_requests": 1,
        "completed_synthesis_requests": int(synthesis_valid),
        "attempts": rows,
        "efficiency": {
            "all_stages": totals,
            "extraction": extraction_totals,
            "synthesis": stage_totals(synthesis_rows),
            "cost_per_valid_extracted_finding_usd": str(extraction_cost / findings)
            if findings and extraction_totals["accounting_complete"]
            else None,
            "pipeline_cost_per_valid_extracted_finding_usd": str(known_spend / findings)
            if findings and totals["accounting_complete"]
            else None,
            "full_pipeline_latency_seconds": None,
            "latency_note": (
                "Sum of measured provider-request latencies is reported separately; "
                "wall time across staged invocations is not measured."
            ),
        },
        "evidence": consolidated,
        "evidence_distribution": [
            {"document_id": doc, "page_number": page} for doc, page in evidence_sources
        ],
        "independent_benchmark_evidence": gold_coverage(
            spans or {}, manifest, steps, attempts, consolidated, corpus
        ),
        "experimental_method": (
            "New extraction/synthesis prompts and schemas; not an identical-prompt comparison"
        ),
        "api_calls": 0,
        "semantic_accuracy": "NOT_ASSESSED",
    }
