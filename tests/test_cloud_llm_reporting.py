"""Offline reporting from wholly synthetic local runs and human declarations."""
# ruff: noqa: F811 - imported generated PDF fixtures intentionally use pytest names.

import copy
import csv
import hashlib
import json
from decimal import Decimal

import httpx
import pytest
from test_cloud_llm_contexts import comparison  # noqa: F401
from test_cloud_llm_execution import cli_arguments, only_mock_http, pilot  # noqa: F401
from test_cloud_llm_extended_execution import retry, truncated  # noqa: F401
from test_external_document_metrics import corpus  # noqa: F401
from test_external_qa_spans import context  # noqa: F401
from test_external_source_span_runner import inputs  # noqa: F401

from evaluation import cloud_llm_reporting as reporting
from evaluation.cloud_llm_contexts import build_comparison_questions
from evaluation.cloud_llm_ledger import ExperimentLedger, ledger_path
from evaluation.cloud_llm_reviews import load_human_reviews, reviewed_spans
from evaluation.external_qa_spans import load_review_context
from evaluation.validate_external_qa_spans import validate_review


def report(truncated, **kwargs):
    return reporting.comparison_report(
        ExperimentLedger.existing(ledger_path()), truncated[1], **kwargs
    )


def test_reports_preserve_billable_truncation_tokens_costs_and_candidate_label_status(truncated):
    result = report(truncated)
    rag, full = result["attempts"]
    assert rag["generation_status"] == "VALID_STRUCTURED_ANSWER"
    assert full["generation_status"] == "OUTPUT_TOKEN_TRUNCATION"
    assert full["output_tokens"] == 100 and full["configured_output_limit"] == 4096
    assert full["reasoning_tokens"] == 6 and full["cached_input_tokens"] == 40
    assert full["cache_write_tokens"] == 10
    assert full["evidence_count"] == 0 and full["evidence_count_status"] == "NOT_AVAILABLE"
    assert full["answer_status"] is None and full["response_validity"] == "INVALID_OR_INCOMPLETE"
    assert rag["citation_status"] == "VERIFIED_QUOTE" and rag["evidence_count"] == 1
    # The synthetic completion cites contract.pdf; the lookup label expects notice.pdf.
    # A verified quote in another document is not expected-document coverage.
    assert rag["all_expected_documents_represented"] is False
    assert rag["expected_document_label_origin"] == "upstream_candidates_not_independent_gold"
    summary = result["summary"]
    expected = sum(Decimal(r["usage_derived_cost_usd"]) for r in result["attempts"])
    assert Decimal(summary["cumulative_usage_derived_cost_usd_all_attempts"]) == expected
    assert Decimal(summary["available_experiment_budget_usd"]) == 20 - expected
    assert result["gold_availability"]["status"] == "NOT_ASSESSED"
    assert rag["human_review"]["status"] == "NOT_ASSESSED"
    pair = result["comparisons"][0]
    assert Decimal(pair["displayed_comparison_cost_usd"]) == expected
    assert Decimal(pair["full_minus_rag_cost_usd"]) == Decimal(
        full["usage_derived_cost_usd"]
    ) - Decimal(rag["usage_derived_cost_usd"])
    assert Decimal(pair["full_to_rag_cost_ratio"]) == Decimal(
        full["usage_derived_cost_usd"]
    ) / Decimal(rag["usage_derived_cost_usd"])
    assert Decimal(rag["estimate_to_usage_cost_ratio"]) > 1
    assert result["api_calls"] == 0 and pair["semantic_accuracy"] == "NOT_ASSESSED"


def test_latest_valid_selection_is_explicit_and_never_hides_failed_attempt_cost(truncated):
    retried, _ = retry(truncated)
    original = report(truncated)
    latest = report(truncated, latest_valid=True)
    assert len(original["attempts"]) == len(latest["attempts"]) == 3
    assert (
        original["comparisons"][0]["selected_attempts"]["full_context"]
        == original["attempts"][1]["reservation_key"]
    )
    assert (
        latest["comparisons"][0]["selected_attempts"]["full_context"]
        == latest["attempts"][2]["reservation_key"]
    )
    assert latest["attempts"][2]["retry_of_attempt"] == truncated[4]["attempt_id"]
    assert latest["attempts"][2]["attempt_id"] == retried["attempt_id"]
    assert original["summary"] == latest["summary"]
    displayed = Decimal(latest["comparisons"][0]["displayed_comparison_cost_usd"])
    cumulative = Decimal(latest["summary"]["cumulative_usage_derived_cost_usd_all_attempts"])
    assert displayed < cumulative
    assert latest["attempts"][1]["generation_status"] == "OUTPUT_TOKEN_TRUNCATION"


def test_json_csv_markdown_and_empty_comparisons_are_generated_offline(truncated, monkeypatch):
    before = ledger_path().read_bytes()
    monkeypatch.setattr(
        httpx, "AsyncClient", lambda *a, **k: pytest.fail("Reporting attempted HTTP")
    )
    result = report(truncated)
    output = truncated[3].with_name("reports")
    reporting.write_reports(output, result)
    assert set(p.name for p in output.iterdir()) == {
        "comparison.json",
        "attempts.csv",
        "comparisons.csv",
        "comparison.md",
    }
    assert json.loads((output / "comparison.json").read_text()) == result
    with (output / "attempts.csv").open(newline="") as stream:
        rows = list(csv.DictReader(stream))
    assert len(rows) == 2 and rows[1]["evidence_count"] == "0"
    assert "OUTPUT_TOKEN_TRUNCATION" in (output / "comparison.md").read_text()
    assert ledger_path().read_bytes() == before
    with pytest.raises(FileExistsError):
        reporting.write_reports(output, result)


def test_missing_responses_and_interrupted_reservations_remain_visible(truncated):
    ledger = ExperimentLedger.existing(ledger_path())
    question = build_comparison_questions(truncated[1])[2]
    entry = ledger.reserve(question, "0.50", truncated[3].with_name("interrupted-run"))
    (truncated[3] / "full_context.json").unlink()
    result = reporting.comparison_report(ledger, truncated[1])
    assert result["attempts"][1]["generation_status"] == "MISSING_RESPONSE_ARTIFACT"
    assert result["attempts"][1]["usage_derived_cost_usd"] == "0.0005"
    interrupted = result["attempts"][2]
    assert interrupted["reservation_key"] == entry["key"]
    assert interrupted["generation_status"] == "INTERRUPTED_EXECUTION"
    assert interrupted["accounting_status"] == "UNCERTAIN" and interrupted["evidence_count"] == 0
    assert result["summary"]["unresolved_attempts"] == 1
    assert Decimal(result["summary"]["committed_cost_or_reservations_usd"]) == ledger.committed()
    assert result["summary"]["cumulative_usage_derived_cost_usd_all_attempts"] == "0.00076"


def test_missing_gold_is_not_candidate_answers_or_automatic_semantic_scores(truncated):
    result = report(truncated)
    assert all(r["ground_truth"]["status"] == "NOT_ASSESSED" for r in result["attempts"])
    assert all(r["semantic_accuracy"] == "NOT_ASSESSED" for r in result["attempts"])
    assert "source_question" not in result and "reference_answer" not in json.dumps(result)


def test_independently_verified_gold_is_revalidated_before_context_and_citation_coverage(
    truncated, inputs
):
    data = truncated[1]
    spans, availability = reviewed_spans(
        inputs["review"], inputs["validation"], inputs["acquisition"], inputs["retrieval"], data
    )
    assert availability["status"] == "INDEPENDENTLY_REVIEWED_SOURCE_LOCATIONS"
    result = report(truncated, spans=spans, gold=availability)
    rag = result["attempts"][0]
    assert rag["ground_truth"]["context_coverage"] == 1
    assert rag["ground_truth"]["citation_coverage"] == 0
    assert rag["semantic_accuracy"] == "NOT_ASSESSED"
    assert result["attempts"][1]["ground_truth"]["context_coverage"] == 1
    assert result["attempts"][1]["ground_truth"]["citation_coverage"] == 0


def test_partial_or_stale_gold_never_produces_coverage(truncated, inputs):
    data = truncated[1]
    raw = json.loads(inputs["review"].read_text())
    raw["questions"][0]["evidence"][0]["review_status"] = "DRAFT"
    inputs["review"].write_text(json.dumps(raw))
    ctx = load_review_context(
        inputs["acquisition"],
        inputs["retrieval"],
        family=data.provenance["family"],
        revision=data.provenance["dataset_revision"],
    )
    saved = validate_review(raw, ctx)
    saved["annotations_sha256"] = hashlib.sha256(inputs["review"].read_bytes()).hexdigest()
    inputs["validation"].write_text(json.dumps(saved))
    spans, availability = reviewed_spans(
        inputs["review"], inputs["validation"], inputs["acquisition"], inputs["retrieval"], data
    )
    assert spans == {} and availability["status"] == "NOT_ASSESSED"
    raw["questions"][0]["evidence"][0]["exact_source_quote"] = "A fabricated quote absent from PDF"
    inputs["review"].write_text(json.dumps(raw))
    with pytest.raises(ValueError, match="stale"):
        reviewed_spans(
            inputs["review"], inputs["validation"], inputs["acquisition"], inputs["retrieval"], data
        )


def human_file(truncated, row, **mutations):
    raw = {
        "schema_version": "cloud-qa-human-reviews-1",
        "provenance": truncated[1].provenance,
        "reviews": [
            {
                "attempt_id": row["attempt_id"],
                "question_id": row["question_id"],
                "strategy": row["strategy"],
                "response_file_sha256": row["response_file_sha256"],
                "reviewer_alias": "synthetic-reviewer",
                "reviewed_by_human": True,
                "reviewed_at": "2026-01-01T00:00:00+00:00",
                "correctness": "CORRECT",
                "completeness": "PARTIAL",
                "notes": "Wholly synthetic test declaration.",
            }
        ],
    }
    raw["reviews"][0].update(mutations)
    path = truncated[3].with_name("human-review.json")
    path.write_text(json.dumps(raw))
    return path


def test_human_verdict_is_separate_hash_bound_and_does_not_modify_model_output(truncated):
    row = report(truncated)["attempts"][0]
    before = (truncated[3] / "e5_rag.json").read_bytes()
    path = human_file(truncated, row)
    reviews, digest = load_human_reviews(path, truncated[1])
    result = report(truncated, human_reviews=reviews)
    assert digest and result["attempts"][0]["human_review"]["correctness"] == "CORRECT"
    assert result["attempts"][0]["human_review"]["completeness"] == "PARTIAL"
    assert result["attempts"][0]["semantic_accuracy"] == "NOT_ASSESSED"
    assert (truncated[3] / "e5_rag.json").read_bytes() == before
    path = human_file(truncated, row, response_file_sha256="f" * 64)
    reviews, _ = load_human_reviews(path, truncated[1])
    with pytest.raises(ValueError, match="unmatched"):
        report(truncated, human_reviews=reviews)


@pytest.mark.parametrize("mutation", ["not_human", "duplicate", "wrong_provenance", "no_timezone"])
def test_human_verdict_safeguards(truncated, mutation):
    row = report(truncated)["attempts"][0]
    path = human_file(truncated, row)
    raw = json.loads(path.read_text())
    if mutation == "not_human":
        raw["reviews"][0]["reviewed_by_human"] = False
    elif mutation == "duplicate":
        raw["reviews"].append(copy.deepcopy(raw["reviews"][0]))
    elif mutation == "wrong_provenance":
        raw["provenance"]["family"] = "other-family"
    else:
        raw["reviews"][0]["reviewed_at"] = "2026-01-01T00:00:00"
    path.write_text(json.dumps(raw))
    with pytest.raises(ValueError):
        load_human_reviews(path, truncated[1])


def test_report_detects_tampered_artifacts_and_different_corpus(truncated):
    path = truncated[3] / "e5_rag.json"
    path.write_bytes(path.read_bytes() + b" ")
    result = report(truncated)
    assert result["attempts"][0]["generation_status"] == "INVALID_ARTIFACT"
    assert result["attempts"][0]["usage_derived_cost_usd"] == "0.00026"
    data = copy.deepcopy(truncated[1])
    data.provenance["family"] = "different-family"
    with pytest.raises(ValueError, match="different source"):
        reporting.comparison_report(ExperimentLedger.existing(ledger_path()), data)


def test_report_runner_and_cli_are_offline_and_preserve_ledger(truncated, inputs, monkeypatch):
    from test_external_benchmark import FAMILY, REVISION

    before = ledger_path().read_bytes()
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **k: pytest.fail("Reporting paid call"))
    output = truncated[3].with_name("full-report-cli")
    assert (
        reporting.main(
            [
                "--acquisition-dir",
                str(inputs["acquisition"]),
                "--retrieval-results",
                str(inputs["retrieval"]),
                "--output-dir",
                str(output),
                "--family",
                FAMILY,
                "--dataset-revision",
                REVISION,
                "--expected-cases",
                "2",
                "--expected-documents",
                "2",
            ]
        )
        == 0
    )
    assert (output / "comparison.json").exists() and ledger_path().read_bytes() == before
    assert (
        reporting.main(
            [
                "--acquisition-dir",
                str(inputs["acquisition"]),
                "--retrieval-results",
                str(inputs["retrieval"]),
                "--output-dir",
                str(output),
            ]
        )
        == 1
    )


def test_empty_experiment_report_and_no_missing_ledger_reset(pilot, monkeypatch):
    config, data, _, _, _, output = pilot
    assert not ledger_path().exists()
    assert (
        reporting.main(
            [
                "--acquisition-dir",
                str(output),
                "--retrieval-results",
                str(output),
                "--output-dir",
                str(output.with_name("missing")),
            ]
        )
        == 1
    )
    assert not ledger_path().exists()
    ledger_path().parent.mkdir(parents=True, exist_ok=True)
    ledger = ExperimentLedger(ledger_path(), config, data)
    result = reporting.comparison_report(ledger, data)
    assert result["attempts"] == [] and result["summary"]["available_experiment_budget_usd"] == "20"
    reporting.write_reports(output.with_name("empty-report"), result)
    assert result["comparisons"][0]["selected_attempts"] == {"e5_rag": None, "full_context": None}


def test_missing_or_invalid_evidence_does_not_become_generation_failure(truncated):
    from test_cloud_llm_extended_execution import completed_body

    selected = truncated[2][1]
    body = completed_body(selected)
    content = json.loads(body["choices"][0]["message"]["content"])
    content["evidence"] = []
    body["choices"][0]["message"]["content"] = json.dumps(content)
    retry(truncated, handler=lambda r: httpx.Response(200, json=body))
    row = report(truncated)["attempts"][2]
    assert row["generation_status"] == "VALID_STRUCTURED_ANSWER"
    assert row["answer_status"] == "ANSWERED" and row["citation_status"] == "MISSING_EVIDENCE"
    assert (
        row["evidence_count"] == 0
        and row["evidence_count_status"] == "VALIDATED_STRUCTURED_RECORDS"
    )


def test_quote_on_verified_gold_document_produces_location_coverage(truncated, inputs):
    from test_cloud_llm_contexts import response

    from evaluation.cloud_llm_contexts import validate_answer

    data = truncated[1]
    prepared = truncated[2][0]
    source = next(s for s in prepared.sources if s.document_name == "notice.pdf")
    answer = response(prepared)
    answer.evidence[0].document_id = source.document_id
    answer.evidence[0].chunk_id = source.chunk_id
    citations = validate_answer(answer, prepared, data)["citations"]
    spans, _ = reviewed_spans(
        inputs["review"], inputs["validation"], inputs["acquisition"], inputs["retrieval"], data
    )
    coverage = reporting.span_coverage(spans[prepared.question_id], prepared, citations, data)
    assert coverage["context_coverage"] == coverage["citation_coverage"] == 1
    assert coverage["semantic_correctness"] == "NOT_ASSESSED"


def test_reports_reject_storage_outside_ignored_root(truncated, tmp_path):
    with pytest.raises(ValueError, match="external"):
        reporting.write_reports(tmp_path.parent / "outside-ignored-data", report(truncated))
