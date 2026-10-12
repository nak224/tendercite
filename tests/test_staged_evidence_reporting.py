"""Offline three-method reports over original generated sources and mocked paid attempts."""

# Imported pytest fixtures are deliberately rebound as injected test parameters.
# ruff: noqa: F811

import asyncio
import hashlib
import json

import httpx
import pytest
from test_cloud_llm_contexts import comparison  # noqa: F401
from test_cloud_llm_execution import (
    KEY,
    completion,
    only_mock_http,  # noqa: F401
)
from test_external_document_metrics import corpus  # noqa: F401
from test_external_qa_spans import context  # noqa: F401
from test_external_source_span_runner import inputs  # noqa: F401
from test_staged_evidence import staged  # noqa: F401
from test_staged_evidence_execution import body_for, invoke, saved  # noqa: F401

from evaluation import external_benchmark
from evaluation import staged_evidence as cli
from evaluation.cloud_llm_contexts import build_comparison_questions
from evaluation.cloud_llm_execution import execute_pair
from evaluation.cloud_llm_ledger import ExperimentLedger, ledger_path
from evaluation.cloud_llm_preflight import preflight
from evaluation.cloud_llm_reporting import comparison_report, run, write_reports
from evaluation.cloud_llm_reviews import HumanVerdict, reviewed_spans
from evaluation.staged_evidence_reporting import staged_report


def test_missing_and_failed_outputs_keep_costs_and_do_not_invent_evidence(saved):
    corpus_data, _, loaded = saved
    before = ledger_path().read_bytes()
    result = staged_report(ExperimentLedger.existing(ledger_path()), loaded, corpus_data)
    assert result["evidence"]["missing_batches"] == [s.batch_id for s in loaded[3]]
    assert result["evidence"]["source_pages_completed"] == 0
    assert result["independent_benchmark_evidence"]["status"] == "NOT_ASSESSED"
    assert ledger_path().read_bytes() == before
    body = body_for(loaded[3][0])
    body["choices"][0].update(finish_reason="length", message={"content": ""})
    invoke(saved, lambda r: httpx.Response(200, json=body))
    result = staged_report(ExperimentLedger.existing(ledger_path()), loaded, corpus_data)
    assert result["efficiency"]["extraction"]["known_usage_derived_cost_usd"] == "0.00026"
    assert result["efficiency"]["all_stages"]["reasoning_tokens"] == 6
    assert result["evidence"]["completed_batches"] == 0
    assert not result["evidence"]["no_relevant_findings_batches"]
    assert result["attempts"][0]["evidence_count"] == 0
    assert result["attempts"][0]["generation_status"] == "OUTPUT_TOKEN_TRUNCATION"
    assert result["efficiency"]["cost_per_valid_extracted_finding_usd"] is None


def test_uncertain_usage_stays_unknown_in_report_and_reservation_stays_counted(saved):
    corpus_data, _, loaded = saved
    body = body_for(loaded[3][0])
    body.pop("usage")
    invoke(saved, lambda r: httpx.Response(200, json=body))
    ledger = ExperimentLedger.existing(ledger_path())
    report = comparison_report(ledger, corpus_data, staged_plan=loaded)
    assert report["summary"]["unresolved_attempts"] == 1
    assert report["staged"]["efficiency"]["all_stages"]["usage_derived_cost_usd"] is None
    assert ledger.committed() > 0


def test_reports_compare_staged_with_truncated_original_full_context(saved):
    corpus_data, _, loaded = saved
    config = loaded[1]
    questions = build_comparison_questions(corpus_data)[:2]
    report, prompts = preflight(config, corpus_data, questions, environment={})
    calls = []

    def handler(request):
        body = completion(questions[0].question_id)
        if calls:
            body["choices"][0].update(finish_reason="length", message={"content": ""})
        calls.append(request)
        return httpx.Response(200, json=body)

    old = asyncio.run(
        execute_pair(
            config,
            corpus_data,
            questions,
            report,
            prompts,
            external_benchmark.EXTERNAL_ROOT / "original-pair",
            confirmed=True,
            environment={"TENDERCITE_LLM_API_KEY": KEY},
            transport=httpx.MockTransport(handler),
        )
    )
    assert old["status"] == "STOPPED"
    invoke(saved, batch_ids=[s.batch_id for s in loaded[3]], maximum=3, output_name="staged-all")
    ledger = ExperimentLedger.existing(ledger_path())
    before = ledger_path().read_bytes()
    result = comparison_report(ledger, corpus_data, staged_plan=loaded)
    assert result["summary"]["attempts"] == 5
    assert result["summary"]["cumulative_usage_derived_cost_usd_all_attempts"] == "0.00130"
    assert result["staged"]["evidence"]["status"] == "COMPLETE"
    assert (
        result["comparisons"][0]["staged_method"]["prompt_comparison"]
        == "CHANGED_EXPERIMENTAL_METHOD"
    )
    assert (
        next(r for r in result["attempts"] if r["strategy"] == "full_context")["generation_status"]
        == "OUTPUT_TOKEN_TRUNCATION"
    )
    assert result["staged"]["efficiency"]["full_pipeline_latency_seconds"] is None
    write_reports(external_benchmark.EXTERNAL_ROOT / "comparison-report", result)
    assert ledger_path().read_bytes() == before
    markdown = (external_benchmark.EXTERNAL_ROOT / "comparison-report/comparison.md").read_text()
    assert "Staged extraction" in markdown and "OUTPUT_TOKEN_TRUNCATION" in markdown
    # Existing offline reports remain usable without specifying a staged plan.
    minimal = comparison_report(ledger, corpus_data)
    assert len(minimal["attempts"]) == 5 and minimal["staged"] is None


def test_complete_independent_gold_is_revalidated_and_partial_gold_is_not_scored(saved, inputs):  # noqa: F811
    corpus_data, _, loaded = saved
    invoke(saved, batch_ids=[s.batch_id for s in loaded[3]], maximum=3)
    spans, gold = reviewed_spans(
        inputs["review"],
        inputs["validation"],
        inputs["acquisition"],
        inputs["retrieval"],
        corpus_data,
    )
    assert gold["status"] == "INDEPENDENTLY_REVIEWED_SOURCE_LOCATIONS"
    report = staged_report(
        ExperimentLedger.existing(ledger_path()), loaded, corpus_data, spans=spans
    )
    coverage = report["independent_benchmark_evidence"]
    assert coverage["original_source_span_coverage"] == 1
    assert coverage["extracted_complete_quote_recall"] == 1
    assert coverage["consolidated_complete_quote_recall"] == 1
    assert not coverage["missing_spans"] and report["semantic_accuracy"] == "NOT_ASSESSED"
    stale = json.loads(inputs["validation"].read_text())
    stale["status"] = "INCOMPLETE"
    inputs["validation"].write_text(json.dumps(stale))
    with pytest.raises(ValueError, match="stale"):
        reviewed_spans(
            inputs["review"],
            inputs["validation"],
            inputs["acquisition"],
            inputs["retrieval"],
            corpus_data,
        )


def test_fresh_but_partial_review_does_not_become_gold(saved, inputs):
    from evaluation.external_qa_spans import load_review_context
    from evaluation.validate_external_qa_spans import validate_review

    corpus_data, _, loaded = saved
    payload = json.loads(inputs["review"].read_text())
    payload["questions"][0]["evidence"][0].update(review_status="DRAFT", reviewer_alias=None)
    inputs["review"].write_text(json.dumps(payload))
    context_data = load_review_context(
        inputs["acquisition"],
        inputs["retrieval"],
        family=corpus_data.provenance["family"],
        revision=corpus_data.provenance["dataset_revision"],
    )
    fresh = validate_review(payload, context_data)
    fresh["annotations_sha256"] = hashlib.sha256(inputs["review"].read_bytes()).hexdigest()
    inputs["validation"].write_text(json.dumps(fresh))
    spans, gold = reviewed_spans(
        inputs["review"],
        inputs["validation"],
        inputs["acquisition"],
        inputs["retrieval"],
        corpus_data,
    )
    assert spans == {} and gold["status"] == "NOT_ASSESSED"
    result = staged_report(
        ExperimentLedger.existing(ledger_path()), loaded, corpus_data, spans=spans
    )
    assert result["independent_benchmark_evidence"]["status"] == "NOT_ASSESSED"


def test_hash_bound_staged_human_review_is_separate_from_model_outputs(saved):
    corpus_data, _, loaded = saved
    invoke(saved)
    ledger = ExperimentLedger.existing(ledger_path())
    entry = ledger.data["entries"][0]
    key = (entry["attempt_id"], entry["question_id"], "staged_extraction")
    from pathlib import Path

    response_path = Path(entry["output_dir"]) / f"{entry['strategy']}.json"
    original_bytes = response_path.read_bytes()
    verdict = HumanVerdict(
        attempt_id=key[0],
        question_id=key[1],
        strategy=key[2],
        response_file_sha256=hashlib.sha256(original_bytes).hexdigest(),
        reviewer_alias="synthetic-reviewer",
        reviewed_by_human=True,
        reviewed_at="2026-10-12T10:00:00Z",
        correctness="UNCERTAIN",
        completeness="PARTIAL",
        notes="Original test-only human declaration",
    )
    result = comparison_report(
        ledger, corpus_data, staged_plan=loaded, human_reviews={key: verdict}
    )
    assert result["attempts"][0]["human_review"]["status"] == "HUMAN_DECLARED_VERDICT"
    assert response_path.read_bytes() == original_bytes
    verdict.response_file_sha256 = "b" * 64
    with pytest.raises(ValueError, match="different/invalid"):
        comparison_report(ledger, corpus_data, staged_plan=loaded, human_reviews={key: verdict})


def test_planning_consolidation_and_reporting_make_zero_calls_and_preserve_ledger(
    saved, inputs, monkeypatch
):  # noqa: F811
    corpus_data, plan_dir, loaded = saved
    invoke(saved, batch_ids=[s.batch_id for s in loaded[3]], maximum=3)
    before = ledger_path().read_bytes()
    monkeypatch.setattr(
        httpx, "AsyncClient", lambda *a, **k: pytest.fail("Offline stage made a paid call")
    )
    monkeypatch.setattr(cli, "load_comparison_corpus", lambda *a, **k: corpus_data)
    shared = [
        "--acquisition-dir",
        str(inputs["acquisition"]),
        "--retrieval-results",
        str(inputs["retrieval"]),
        "--plan-dir",
        str(plan_dir),
    ]
    folder = external_benchmark.EXTERNAL_ROOT / "offline-consolidation"
    assert cli.main(["consolidate", *shared, "--output-dir", str(folder)]) == 0
    assert (
        cli.main(
            [
                "synthesize",
                *shared,
                "--consolidation-dir",
                str(folder),
                "--question-id",
                loaded[0]["question_id"],
                "--output-dir",
                str(external_benchmark.EXTERNAL_ROOT / "synthesis-dry"),
            ]
        )
        == 0
    )
    report = run(
        inputs["acquisition"],
        inputs["retrieval"],
        external_benchmark.EXTERNAL_ROOT / "report-offline",
        staged_plan_dir=plan_dir,
        family=corpus_data.provenance["family"],
        revision=corpus_data.provenance["dataset_revision"],
        expected_cases=2,
        expected_documents=2,
    )
    assert report["api_calls"] == 0 and report["staged"]["evidence"]["status"] == "COMPLETE"
    assert ledger_path().read_bytes() == before


def test_no_source_containing_outputs_allowed_in_committed_paths(saved, tmp_path):
    from evaluation.staged_evidence_plan import save_plan

    _, _, loaded = saved
    outside = tmp_path.parent / "unsafe-source-plan"
    with pytest.raises(ValueError, match="ignored"):
        save_plan(outside, loaded[0], loaded[4])
    assert not outside.exists()
