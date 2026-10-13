"""Sealed synthetic historical files: replay/report only, no HTTP or new reservations."""

# Imported pytest fixtures are intentionally rebound as fixture parameters.
# ruff: noqa: F811

import json

import httpx
import pytest
from test_cloud_llm_contexts import comparison  # noqa: F401
from test_external_document_metrics import corpus  # noqa: F401
from test_external_qa_spans import context  # noqa: F401
from test_external_source_span_runner import inputs  # noqa: F401
from test_staged_evidence import extraction, finding, staged  # noqa: F401
from test_staged_evidence_execution import saved  # noqa: F401

from evaluation import external_benchmark
from evaluation.cloud_llm_execution import response_record
from evaluation.cloud_llm_ledger import ExperimentLedger, ledger_path
from evaluation.cloud_llm_reporting import comparison_report, main, run
from evaluation.external_qa_spans import read_local_json, write_new_json
from evaluation.legal_reference_intelligence import derive_legal_references
from evaluation.staged_evidence_consolidation import prepared_synthesis, validate_extraction
from evaluation.staged_evidence_plan import digest, load_plan, stage_config
from evaluation.staged_evidence_reporting import staged_report
from evaluation.staged_evidence_schemas import (
    EXTRACTION_PROMPT_VERSION,
    EXTRACTION_SCHEMA_VERSION,
    SYNTHESIS_PROMPT_VERSION,
    SYNTHESIS_SCHEMA_VERSION,
    AtomicExtraction,
)
from tendercite.services.llm.openai_compatible import CompletionResponse, OpenAICompatibleLLM


@pytest.fixture
def history(saved, monkeypatch):
    corpus_data, directory, loaded = saved
    manifest, config, settings, steps, _, plan_hash = loaded
    ledger = ExperimentLedger.existing(ledger_path())
    effective = stage_config(config, settings, "extraction")
    # Manufacture original historical artifacts directly; no executor/HTTP/reserve/reconcile.
    for index, (step, batch) in enumerate(zip(steps, manifest["batches"], strict=True)):
        row = batch["preflight"]
        attempt_id, output = (
            f"original-synthetic-{index}",
            external_benchmark.EXTERNAL_ROOT / f"history-{index}",
        )
        metadata = {
            "version": "staged-ledger-step-1",
            "transmission_confirmed": True,
            "plan_id": manifest["plan_id"],
            "step_id": step.step_id,
            "stage": "extraction",
            "batch_id": step.batch_id,
            "plan_dir": str(directory),
            "plan_file_sha256": plan_hash,
            "request_payload_sha256": row["request_payload_sha256"],
            "input_source_sha256": digest([s.model_dump() for s in step.sources]),
            "configuration_sha256": digest(effective.model_dump(mode="json")),
        }
        answer = extraction(step, [] if index == 0 else [finding(step)])
        body = {
            "model": effective.model,
            "choices": [
                {"finish_reason": "stop", "message": {"content": answer.model_dump_json()}}
            ],
            "usage": {
                "prompt_tokens": 100,
                "completion_tokens": 20,
                "total_tokens": 120,
                "completion_tokens_details": {"reasoning_tokens": 0},
            },
        }
        request_id = f"req-original-synthetic-{index}"
        record, accounting = response_record(
            CompletionResponse(json.dumps(body).encode(), 200, request_id, 0.2),
            step,
            corpus_data,
            effective,
            response_model=AtomicExtraction,
            validate_output=validate_extraction,
        )
        key = digest([attempt_id, step.strategy])
        record.update(
            question_id=step.question_id,
            strategy=step.strategy,
            stage="extraction",
            staged_step=metadata,
            attempt_id=attempt_id,
            reservation_key=key,
            configuration=effective.model_dump(mode="json"),
            source_provenance=corpus_data.provenance,
            request_payload_sha256=row["request_payload_sha256"],
            preflight_estimate=row,
            prompt_version=EXTRACTION_PROMPT_VERSION,
            response_schema_version=EXTRACTION_SCHEMA_VERSION,
        )
        file = output / f"{step.strategy}.json"
        write_new_json(file, record)
        _, sha = read_local_json(file)
        ledger.data["entries"].append(
            {
                "key": key,
                "attempt_id": attempt_id,
                "question_id": step.question_id,
                "strategy": step.strategy,
                "reserved_cost": row["estimated_cost_with_safety_margin"],
                "status": "SETTLED",
                "output_dir": str(output),
                "staged_step": metadata,
                "usage_derived_cost": accounting["usage_derived_cost"],
                "response_file_sha256": sha,
                "provider_request_id": request_id,
                "http_status": 200,
            }
        )
    # Fixture construction only. Below this line all historical bytes are immutable.
    ledger.path.write_text(json.dumps(ledger.data), encoding="utf-8")
    snapshots = {
        p: p.read_bytes()
        for p in external_benchmark.EXTERNAL_ROOT.rglob("*")
        if p.is_file() and p.suffix != ".lock"  # OS lock owner metadata is not ledger history.
    }

    def forbidden(*args, **kwargs):
        pytest.fail("Offline legal review attempted HTTP, a reservation or ledger mutation")

    monkeypatch.setattr(httpx, "Client", forbidden)
    monkeypatch.setattr(httpx, "AsyncClient", forbidden)
    monkeypatch.setattr(OpenAICompatibleLLM, "request_completion", forbidden)
    monkeypatch.setattr(ExperimentLedger, "reserve", forbidden)
    monkeypatch.setattr(ExperimentLedger, "reconcile", forbidden)
    monkeypatch.setattr("evaluation.cloud_llm_ledger.atomic_save", forbidden)
    yield corpus_data, directory, loaded, snapshots
    changed = [
        str(p.relative_to(external_benchmark.EXTERNAL_ROOT))
        for p, data in snapshots.items()
        if p.read_bytes() != data
    ]
    assert not changed


def test_old_plan_core_step_id_and_ledger_survive_derived_reporting(history):
    corpus_data, directory, loaded, _ = history
    assert load_plan(directory, corpus_data) == loaded
    ledger = ExperimentLedger.existing(ledger_path())
    committed = ledger.committed()
    report = staged_report(ledger, loaded, corpus_data)
    original = report["evidence"]
    step = prepared_synthesis(loaded[0], original, corpus_data)
    layer = derive_legal_references(original, corpus_data)
    assert layer == report["legal_references"]
    assert prepared_synthesis(loaded[0], original, corpus_data) == step
    assert loaded[0]["prompt_versions"] == [EXTRACTION_PROMPT_VERSION, SYNTHESIS_PROMPT_VERSION]
    assert loaded[0]["schema_versions"] == [EXTRACTION_SCHEMA_VERSION, SYNTHESIS_SCHEMA_VERSION]
    assert ledger.committed() == committed
    assert report["attempts"][0]["citation_status"] == "NO_CITATIONS_APPLICABLE"
    assert all(r["reasoning_tokens"] == 0 for r in report["attempts"])
    for row, batch in zip(report["attempts"], loaded[0]["batches"], strict=True):
        comparison = row["estimated_vs_reported"]
        assert comparison["conservative_input_tokens"] == batch["preflight"]["input_token_estimate"]
        assert comparison["maximum_output_tokens"] == 100
        assert comparison["provider_input_tokens"] == 100
        assert comparison["provider_completion_tokens"] == 20
        assert comparison["usage_derived_cost_usd"] == row["usage_derived_cost_usd"]
        assert comparison["reserved_cost_with_margin_usd"] == row["original_reserved_usd"]


def test_offline_cli_exports_review_and_retains_historical_costs(history, inputs):
    corpus_data, directory, _, _ = history
    output = external_benchmark.EXTERNAL_ROOT / "offline-report"
    assert (
        main(
            [
                "--staged-plan",
                str(directory),
                "--acquisition-dir",
                str(inputs["acquisition"]),
                "--retrieval-results",
                str(inputs["retrieval"]),
                "--output-dir",
                str(output),
                "--family",
                corpus_data.provenance["family"],
                "--dataset-revision",
                corpus_data.provenance["dataset_revision"],
                "--expected-cases",
                "2",
                "--expected-documents",
                "2",
            ]
        )
        == 0
    )
    report, _ = read_local_json(output / "comparison.json")
    layer, _ = read_local_json(output / "legal-references.json")
    review, _ = read_local_json(output / "legal-meaning-review-template.json")
    assert layer == report["staged"]["legal_references"]
    assert all(r["status"] == "DRAFT" for r in review["reviews"])
    assert report["summary"]["cumulative_usage_derived_cost_usd_all_attempts"] == "0.00078"
    assert report["summary"]["budget_usd"] == "20" and report["api_calls"] == 0
    assert "Derived legal references" in (output / "comparison.md").read_text()
    assert layer["semantic_accuracy"] == "NOT_SCORED"
    plain = comparison_report(ExperimentLedger.existing(ledger_path()), corpus_data)
    assert plain["staged"] is None and len(plain["attempts"]) == 3


def test_review_file_hash_is_reported_without_overwriting_original_answers(history, inputs):
    corpus_data, directory, _, _ = history
    cohort = {
        "family": corpus_data.provenance["family"],
        "revision": corpus_data.provenance["dataset_revision"],
        "expected_cases": 2,
        "expected_documents": 2,
    }
    first_dir = external_benchmark.EXTERNAL_ROOT / "draft-review-export"
    first = run(
        inputs["acquisition"], inputs["retrieval"], first_dir, staged_plan_dir=directory, **cohort
    )
    review, _ = read_local_json(first_dir / "legal-meaning-review-template.json")
    review["reviews"][0].update(
        status="VERIFIED",
        author_alias="synthetic-a",
        reviewer_alias="synthetic-b",
        reviewed_at="2026-10-12T12:00:00Z",
        review_notes="Synthetic human declaration.",
    )
    review_path = external_benchmark.EXTERNAL_ROOT / "meaning-review.json"
    write_new_json(review_path, review)
    second = run(
        inputs["acquisition"],
        inputs["retrieval"],
        external_benchmark.EXTERNAL_ROOT / "reviewed-export",
        staged_plan_dir=directory,
        legal_meaning_review=review_path,
        **cohort,
    )
    assert second["legal_meaning_review_file_sha256"] == read_local_json(review_path)[1]
    assert first["staged"]["evidence"] == second["staged"]["evidence"]
    assert (
        second["staged"]["legal_references"]["associated_findings"][0]["semantic_status"]
        == "HUMAN_VERIFIED_MEANING"
    )
    assert (
        first["summary"]["committed_cost_or_reservations_usd"]
        == second["summary"]["committed_cost_or_reservations_usd"]
    )


def test_tampered_historical_response_remains_quarantined_and_fee_preserved(history):
    corpus_data, _, loaded, snapshots = history
    ledger = ExperimentLedger.existing(ledger_path())
    entry = ledger.data["entries"][1]
    file = next(p for p in snapshots if p.name == entry["strategy"] + ".json")
    before = file.read_bytes()
    try:
        file.write_bytes(before + b" ")  # Even JSON-preserving mutation changes the original hash.
        report = staged_report(ledger, loaded, corpus_data)
        assert report["evidence"]["status"] == "INCOMPLETE"
        assert report["attempts"][1]["generation_status"] == "INVALID_ARTIFACT"
        assert report["attempts"][1]["usage_derived_cost_usd"] == entry["usage_derived_cost"]
        assert len(report["legal_references"]["associated_findings"]) == 1
    finally:
        file.write_bytes(before)


def test_missing_original_ledger_is_not_created(saved, inputs, monkeypatch):
    corpus_data, directory, _ = saved
    ledger_path().unlink()  # Synthetic empty fixture only.
    monkeypatch.setattr(ExperimentLedger, "__init__", lambda *a, **k: pytest.fail("New ledger"))
    with pytest.raises(FileNotFoundError):
        run(
            inputs["acquisition"],
            inputs["retrieval"],
            external_benchmark.EXTERNAL_ROOT / "missing-ledger",
            staged_plan_dir=directory,
            family=corpus_data.provenance["family"],
            revision=corpus_data.provenance["dataset_revision"],
            expected_cases=2,
            expected_documents=2,
        )
    assert not ledger_path().exists()


def test_legal_annotations_require_a_plan(history):
    corpus_data, _, _, _ = history
    with pytest.raises(ValueError, match="staged-plan"):
        comparison_report(ExperimentLedger.existing(ledger_path()), corpus_data, legal_alias_map={})
