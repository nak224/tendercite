"""Offline, retry-aware comparison reports; no provider calls or ledger writes."""

import argparse
import csv
import hashlib
import json
import logging
from collections import defaultdict
from decimal import Decimal
from pathlib import Path

from evaluation.cloud_llm_contexts import build_comparison_questions, load_comparison_corpus
from evaluation.cloud_llm_execution import response_record
from evaluation.cloud_llm_ledger import ExperimentLedger, amount, experiment_lock, ledger_path
from evaluation.cloud_llm_preflight import ComparisonConfig, estimate_input_tokens, prepared_payload
from evaluation.cloud_llm_reviews import load_human_reviews, reviewed_spans
from evaluation.external_benchmark import confined
from evaluation.external_document_cases import DATASET_REVISION, FAMILY
from evaluation.external_qa_spans import read_local_json, write_new_json
from evaluation.ted_acquisition import timestamp
from tendercite.services.llm.openai_compatible import CompletionResponse
from tendercite.services.text import normalize_quote


def ratio(numerator, denominator):
    return (
        str(amount(numerator) / amount(denominator))
        if denominator and amount(denominator)
        else None
    )


def span_coverage(spans, prepared, citations, corpus):
    if not spans:
        return {"status": "NOT_ASSESSED", "context_coverage": None, "citation_coverage": None}
    in_context, in_citations = 0, 0
    for span in spans:
        doc = corpus.mapping[span["source_document"]]
        page = span["page_number"]
        quote = normalize_quote(span["exact_source_quote"])
        in_context += any(
            s.document_id == doc and s.page_number == page and quote in normalize_quote(s.text)
            for s in prepared.sources
        )
        in_citations += any(
            c["validation"]["grounding_status"] == "VERIFIED_QUOTE"
            and c["citation"]["document_id"] == doc
            and c["citation"]["page_number"] == page
            and quote in normalize_quote(c["citation"]["quote"])
            for c in citations
        )
    return {
        "status": "REVIEWED_SOURCE_SPAN_OCCURRENCE_ONLY",
        "verified_span_count": len(spans),
        "spans_in_supplied_context": in_context,
        "spans_in_verified_citations": in_citations,
        "context_coverage": in_context / len(spans),
        "citation_coverage": in_citations / len(spans),
        "semantic_correctness": "NOT_ASSESSED",
    }


def attempt_row(ledger, entry, corpus, spans, human_reviews, used_reviews):
    qid, strategy = entry["question_id"], entry["strategy"]
    original = corpus.questions.get(qid)
    if original is None or strategy not in ("e5_rag", "full_context"):
        raise ValueError("Ledger attempt does not belong to the selected question cohort")
    known = entry["status"] in ("SETTLED", "OVER_ESTIMATE")
    expected = [corpus.mapping[r.filename] for r in original.source_documents]
    row = {
        "attempt_id": ledger.attempt_id(entry),
        "reservation_key": entry["key"],
        "question_id": qid,
        "question_type": original.question_type,
        "strategy": strategy,
        "retry_of_attempt": entry.get("retry_of_attempt"),
        "retry_of_key": entry.get("retry_of_key"),
        "historical_ledger_status": entry["status"],
        "effective_ledger_status": ledger.effective_status(entry),
        "original_reserved_usd": entry["reserved_cost"],
        "usage_derived_cost_usd": entry.get("usage_derived_cost") if known else None,
        "committed_cost_or_reservation_usd": str(ledger.entry_cost(entry)),
        "original_preflight_estimate_usd": entry["reserved_cost"],
        "estimate_to_usage_cost_ratio": ratio(
            entry["reserved_cost"], entry.get("usage_derived_cost")
        )
        if known
        else None,
        "response_file": str(Path(entry["output_dir"]) / f"{strategy}.json"),
        "generation_status": "INTERRUPTED_EXECUTION"
        if entry["status"] == "RESERVED"
        else "MISSING_RESPONSE_ARTIFACT",
        "response_validity": "INVALID_OR_INCOMPLETE",
        "accounting_status": "UNCERTAIN" if not known else "SETTLED_USAGE_ARTIFACT_UNAVAILABLE",
        "answer_status": None,
        "completion_status": None,
        "input_tokens": None,
        "output_tokens": None,
        "reasoning_tokens": None,
        "cached_input_tokens": None,
        "cache_write_tokens": None,
        "evidence_count": 0,
        "evidence_count_status": "NOT_AVAILABLE",
        "citation_status": "NOT_ASSESSED",
        "expected_source_documents": expected,
        "expected_document_label_origin": "upstream_candidates_not_independent_gold",
        "represented_expected_documents": [],
        "all_expected_documents_represented": None,
        "source_references": [],
        "quote_failures": [],
        "ground_truth": {"status": "NOT_ASSESSED"},
        "human_review": {"status": "NOT_ASSESSED"},
        "semantic_accuracy": "NOT_ASSESSED",
    }
    try:
        record, digest = read_local_json(Path(row["response_file"]))
        if (
            record.get("reservation_key") != entry["key"]
            or record.get("question_id") != qid
            or record.get("strategy") != strategy
            or record.get("source_provenance") != corpus.provenance
            or (entry.get("response_file_sha256") and entry["response_file_sha256"] != digest)
            or (entry.get("attempt_id") and record.get("attempt_id") != entry["attempt_id"])
        ):
            raise ValueError("Response artifact does not match ledger/source provenance")
        config = ComparisonConfig.model_validate(record["configuration"])
        binding = ledger.data["binding"]
        if (
            (config.base_url, config.model) != (binding["provider"], binding["model"])
            or config.pricing.model_dump(mode="json") != binding["pricing"]
            or (config.usage_pricing.model_dump(mode="json") if config.usage_pricing else None)
            != binding["usage_pricing"]
        ):
            raise ValueError("Response configuration differs from the ledger pricing/model binding")
        prepared = next(
            q
            for q in build_comparison_questions(corpus, top_k=config.rag_top_k)
            if (q.question_id, q.strategy) == (qid, strategy)
        )
        _, wire = estimate_input_tokens(prepared_payload(config, prepared), config)
        if record.get("request_payload_sha256") != hashlib.sha256(wire).hexdigest():
            raise ValueError("Saved request payload/source hash does not match reconstructed input")
        row["response_file_sha256"] = digest
        raw = record["raw_response"].encode()
        diagnostic, accounting = response_record(
            CompletionResponse(
                raw,
                record["http_status"],
                record.get("provider_request_id"),
                record.get("latency_seconds", 0),
            ),
            prepared,
            corpus,
            config,
        )
        row.update(
            {
                "configured_output_limit": config.max_output_tokens,
                "request_payload_sha256": record["request_payload_sha256"],
                "preflight_input_token_estimate": record.get("preflight_estimate", {}).get(
                    "input_token_estimate"
                ),
                "experimental_configuration_difference": record["retry"].get(
                    "configuration_difference"
                )
                if isinstance(record.get("retry"), dict)
                else None,
                "model_capabilities": config.model_dump(mode="json")["model_capabilities"],
                "actual_model": diagnostic.get("actual_model"),
                "provider_request_id": record.get("provider_request_id"),
                "http_status": record["http_status"],
                "latency_seconds": record.get("latency_seconds"),
                "generation_status": diagnostic["generation_status"],
                "response_validity": diagnostic["response_validity"],
                "recorded_response_validity": record.get("response_validity"),
                "completion_status": diagnostic.get("completion_status"),
                "accounting_status": diagnostic["accounting_status"],
                "provider_error_code": diagnostic.get("provider_error_code"),
                "diagnostic_error": diagnostic.get("error"),
            }
        )
        if accounting:
            for out, key in (
                ("input_tokens", "prompt_tokens"),
                ("output_tokens", "completion_tokens"),
                ("reasoning_tokens", "reasoning_tokens"),
                ("cached_input_tokens", "cached_input_tokens"),
                ("cache_write_tokens", "cache_write_tokens"),
            ):
                row[out] = accounting[key]
            row["total_tokens"] = accounting["total_tokens"]
            if known and amount(accounting["usage_derived_cost"]) != amount(
                entry["usage_derived_cost"]
            ):
                raise ValueError("Provider usage differs from preserved ledger cost")
        elif known:
            row["accounting_status"] = "SETTLED_USAGE_ARTIFACT_UNAVAILABLE"
        answer = diagnostic.get("structured_answer")
        validation = diagnostic.get("citation_validation", {})
        citations = validation.get("citations", [])
        if answer:
            row["answer_status"] = answer["status"]
            row["evidence_count"] = len(answer["evidence"])
            row["evidence_count_status"] = "VALIDATED_STRUCTURED_RECORDS"
            row["citation_status"] = validation["citation_status"]
            row["answer_classification"] = validation["response_classification"]
        row["source_references"] = [
            {k: c["citation"][k] for k in ("document_id", "page_number", "chunk_id")}
            for c in citations
        ]
        row["quote_failures"] = [
            {
                "index": i,
                "reason": c["failure_reason"],
                "status": c["validation"]["grounding_status"],
            }
            for i, c in enumerate(citations)
            if c["failure_reason"]
        ]
        represented = {
            c["citation"]["document_id"]
            for c in citations
            if c["validation"]["grounding_status"] == "VERIFIED_QUOTE"
        }
        row["represented_expected_documents"] = sorted(set(expected) & represented)
        row["all_expected_documents_represented"] = (
            set(expected).issubset(represented) if answer else None
        )
        row["ground_truth"] = span_coverage(spans.get(qid, []), prepared, citations, corpus)
        key = (row["attempt_id"], qid, strategy)
        if review := human_reviews.get(key):
            if review.response_file_sha256 != digest:
                raise ValueError("Human verdict is bound to a different response file")
            row["human_review"] = {
                "status": "HUMAN_DECLARED_VERDICT",
                **review.model_dump(mode="json"),
            }
            used_reviews.add(key)
    except (OSError, ValueError, KeyError, TypeError, AttributeError, StopIteration) as exc:
        row["artifact_error"] = str(exc)
        if not isinstance(exc, FileNotFoundError):
            row["generation_status"] = "INVALID_ARTIFACT"
            row["response_validity"] = "INVALID_OR_INCOMPLETE"
    return row


def comparison_report(
    ledger, corpus, *, spans=None, gold=None, human_reviews=None, latest_valid=False
):
    if ledger.data["binding"]["provenance"] != corpus.provenance:
        raise ValueError("Experiment ledger belongs to different source/retrieval provenance")
    used_reviews = set()
    reviews = human_reviews or {}
    rows = [
        attempt_row(ledger, e, corpus, spans or {}, reviews, used_reviews)
        for e in ledger.data["entries"]
    ]
    if set(reviews) != used_reviews:
        raise ValueError("Human review targets are unmatched or have invalid artifacts")
    groups = defaultdict(list)
    for row in rows:
        groups[(row["question_id"], row["strategy"])].append(row)
    comparisons = []
    for qid, question in corpus.questions.items():
        selected = {}
        for strategy in ("e5_rag", "full_context"):
            attempts = groups[(qid, strategy)]
            if latest_valid:
                valid = [r for r in attempts if r["response_validity"] == "VALID_STRUCTURED_ANSWER"]
                chosen = valid[-1] if valid else None
            else:
                chosen = attempts[0] if attempts else None
            selected[strategy] = chosen
        rag, full = selected["e5_rag"], selected["full_context"]
        rc = rag["usage_derived_cost_usd"] if rag else None
        fc = full["usage_derived_cost_usd"] if full else None
        comparisons.append(
            {
                "question_id": qid,
                "question_type": question.question_type,
                "selection": "LATEST_VALID_EXPLICIT" if latest_valid else "ORIGINAL_ATTEMPTS",
                "selected_attempts": {
                    k: v["reservation_key"] if v else None for k, v in selected.items()
                },
                "attempt_count": sum(len(groups[(qid, s)]) for s in ("e5_rag", "full_context")),
                "displayed_comparison_cost_usd": str(amount(rc) + amount(fc))
                if rc is not None and fc is not None
                else None,
                "full_minus_rag_cost_usd": str(amount(fc) - amount(rc))
                if rc is not None and fc is not None
                else None,
                "full_to_rag_cost_ratio": ratio(fc, rc)
                if fc is not None and rc is not None
                else None,
                "semantic_accuracy": "NOT_ASSESSED",
            }
        )
    known_spend = sum(
        (
            amount(e["usage_derived_cost"])
            for e in ledger.data["entries"]
            if e["status"] in ("SETTLED", "OVER_ESTIMATE")
        ),
        Decimal("0"),
    )
    return {
        "schema_version": "cloud-qa-comparison-report-1",
        "recorded_at": timestamp(),
        "api_calls": 0,
        "provenance": corpus.provenance,
        "gold_availability": gold or {"status": "NOT_ASSESSED"},
        "selection": "LATEST_VALID_EXPLICIT" if latest_valid else "ORIGINAL_ATTEMPTS",
        "summary": {
            "eligible_questions": len(corpus.questions),
            "attempts": len(rows),
            "cumulative_usage_derived_cost_usd_all_attempts": str(known_spend),
            "prior_estimated_spend_usd": ledger.data["binding"]["prior_estimated_spend"],
            "committed_cost_or_reservations_usd": str(ledger.committed()),
            "available_experiment_budget_usd": str(ledger.limit - ledger.committed()),
            "budget_usd": "20",
            "currency": "USD",
            "manual_zero_reconciliations": len(ledger.manual_zero),
            "unresolved_attempts": sum(
                r["effective_ledger_status"] in ("RESERVED", "UNCERTAIN", "OVER_ESTIMATE")
                for r in rows
            ),
            "failed_or_truncated_attempts": sum(
                r["response_validity"] != "VALID_STRUCTURED_ANSWER" for r in rows
            ),
            "final_provider_billing": "REQUIRES_PROVIDER_DASHBOARD_RECONCILIATION",
        },
        "attempts": rows,
        "comparisons": comparisons,
        "interpretation": (
            "Verified quotes and reviewed-span containment establish textual occurrence, "
            "not answer correctness or completeness. Upstream document references are "
            "candidate labels. Usage-derived costs are not invoices."
        ),
    }


def write_reports(directory, report):
    directory = confined(directory)
    directory.mkdir(parents=True, exist_ok=False)
    write_new_json(directory / "comparison.json", report)
    keys = sorted({key for row in report["attempts"] for key in row})
    with (directory / "attempts.csv").open("x", encoding="utf-8", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=keys or ["question_id", "strategy"])
        writer.writeheader()
        for row in report["attempts"]:
            values = {
                key: json.dumps(value, ensure_ascii=False)
                if isinstance(value, (dict, list))
                else value
                for key, value in row.items()
            }
            for key, value in values.items():
                if isinstance(value, str) and value.startswith(("=", "+", "-", "@")):
                    values[key] = (
                        "'" + value
                    )  # Keep spreadsheet software from executing source metadata.
            writer.writerow(values)
    with (directory / "comparisons.csv").open("x", encoding="utf-8", newline="") as output:
        keys = list(report["comparisons"][0]) if report["comparisons"] else ["question_id"]
        writer = csv.DictWriter(output, fieldnames=keys)
        writer.writeheader()
        for row in report["comparisons"]:
            writer.writerow(
                {key: json.dumps(v) if isinstance(v, dict) else v for key, v in row.items()}
            )
    summary = report["summary"]
    lines = [
        "# Offline cloud QA comparison",
        "",
        report["interpretation"],
        "",
        f"Selection: {report['selection']}. All {summary['attempts']} attempts remain visible.",
        "Cumulative usage-derived cost: "
        f"{summary['cumulative_usage_derived_cost_usd_all_attempts']} USD.",
        f"Committed costs/reservations: {summary['committed_cost_or_reservations_usd']} USD; "
        f"available budget: {summary['available_experiment_budget_usd']} USD.",
        f"Gold availability: {report['gold_availability']['status']}. "
        "Semantic accuracy: NOT_ASSESSED.",
        "",
        "| Question | Strategy / attempt | Generation | Evidence | Usage cost USD |",
        "| --- | --- | --- | --- | --- |",
    ]
    for row in report["attempts"]:
        cells = [
            row["question_id"],
            f"{row['strategy']} / {row['attempt_id']}",
            row["generation_status"],
            f"{row['citation_status']} ({row['evidence_count']})",
            row["usage_derived_cost_usd"],
        ]
        lines.append(
            "| " + " | ".join(str(v).replace("|", "\\|").replace("\n", " ") for v in cells) + " |"
        )
    lines.extend(
        [
            "",
            "Displayed comparison costs and ratios are in comparisons.csv; "
            "all attempts and diagnostics are in attempts.csv and comparison.json.",
            "Provider billing requires dashboard reconciliation. "
            "No API calls were made by reporting.",
        ]
    )
    (directory / "comparison.md").write_text(
        "\n".join(lines) + "\n", encoding="utf-8", newline="\n"
    )


def run(
    acquisition_dir,
    retrieval_results,
    output_dir,
    *,
    latest_valid=False,
    annotations=None,
    validation=None,
    human_review=None,
    **cohort,
):
    output_dir = confined(output_dir)
    if output_dir.exists():
        raise ValueError("Use a fresh ignored report directory")
    corpus = load_comparison_corpus(acquisition_dir, retrieval_results, **cohort)
    spans, gold = reviewed_spans(
        annotations, validation, acquisition_dir, retrieval_results, corpus
    )
    reviews, review_hash = load_human_reviews(human_review, corpus)
    with experiment_lock(ledger_path()):
        ledger = ExperimentLedger.existing(ledger_path())
        report = comparison_report(
            ledger, corpus, spans=spans, gold=gold, human_reviews=reviews, latest_valid=latest_valid
        )
        _, report["ledger_sha256"] = read_local_json(ledger_path())
    report["human_review_file_sha256"] = review_hash
    write_reports(output_dir, report)
    return report


def main(argv=None):
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--acquisition-dir", type=Path, required=True)
    cli.add_argument("--retrieval-results", type=Path, required=True)
    cli.add_argument("--output-dir", type=Path, required=True)
    cli.add_argument(
        "--latest-valid",
        action="store_true",
        help="Explicitly display latest valid answer per strategy; retain every attempt",
    )
    cli.add_argument("--annotations", type=Path)
    cli.add_argument("--validation", type=Path)
    cli.add_argument("--human-review", type=Path)
    cli.add_argument("--family", default=FAMILY)
    cli.add_argument("--dataset-revision", default=DATASET_REVISION)
    cli.add_argument("--expected-cases", type=int, default=9)
    cli.add_argument("--expected-documents", type=int, default=3)
    args = cli.parse_args(argv)
    try:
        report = run(
            args.acquisition_dir,
            args.retrieval_results,
            args.output_dir,
            latest_valid=args.latest_valid,
            annotations=args.annotations,
            validation=args.validation,
            human_review=args.human_review,
            family=args.family,
            revision=args.dataset_revision,
            expected_cases=args.expected_cases,
            expected_documents=args.expected_documents,
        )
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        logging.error("Offline comparison report rejected: %s", exc)
        return 1
    print(json.dumps(report["summary"], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
