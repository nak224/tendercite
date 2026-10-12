"""Shared-ledger staged HTTP uses MockTransport only; no real provider or billing actions."""

import asyncio
import copy
import json
from decimal import Decimal

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
from test_staged_evidence import extraction, finding, staged  # noqa: F401

from evaluation import external_benchmark
from evaluation import staged_evidence as cli
from evaluation import staged_evidence_execution as execution
from evaluation.cloud_llm_contexts import build_comparison_questions
from evaluation.cloud_llm_execution import execute_pair
from evaluation.cloud_llm_ledger import ExperimentLedger, atomic_save, experiment_lock, ledger_path
from evaluation.cloud_llm_preflight import preflight
from evaluation.external_qa_spans import read_local_json, write_new_json
from evaluation.staged_evidence_consolidation import consolidate
from evaluation.staged_evidence_execution import (
    execute_extractions,
    execute_synthesis,
    extraction_attempts,
)
from evaluation.staged_evidence_plan import load_plan, save_plan


@pytest.fixture
def saved(staged):  # noqa: F811
    corpus_data, config, settings, manifest, _, requests = staged
    root = external_benchmark.EXTERNAL_ROOT
    directory = root / "source-plan"
    save_plan(directory, manifest, requests)
    with experiment_lock(ledger_path()):
        ExperimentLedger(ledger_path(), config, corpus_data)  # Original synthetic temp root only.
    loaded = load_plan(directory, corpus_data)
    return corpus_data, directory, loaded


def body_for(step, *, empty=False):
    body = completion(step.question_id)
    body["choices"][0]["message"]["content"] = extraction(
        step, [] if empty else [finding(step)]
    ).model_dump_json()
    return body


def invoke(
    saved_data,
    handler=None,
    *,
    batch_ids=None,
    continue_remaining=False,
    maximum=1,
    output_name="paid-mock-1",
    confirmed=True,
    environment=None,
    question_id=None,
):
    corpus_data, directory, loaded = saved_data
    manifest, config, settings, steps, _, plan_hash = loaded
    step_map = {s.batch_id: s for s in steps}

    def default_handler(request):
        payload = json.loads(request.content)
        raw = payload["messages"][1]["content"].split("\n\nReturn JSON matching this schema:")[0]
        batch_id = json.loads(raw)["batch_id"]
        return httpx.Response(
            200, json=body_for(step_map[batch_id]), headers={"x-request-id": "req-staged-mock"}
        )

    return asyncio.run(
        execute_extractions(
            manifest,
            config,
            settings,
            steps,
            corpus_data,
            directory,
            plan_hash,
            external_benchmark.EXTERNAL_ROOT / output_name,
            question_id=question_id or manifest["question_id"],
            batch_ids=(
                [steps[0].batch_id]
                if batch_ids is None and not continue_remaining
                else batch_ids or []
            ),
            continue_remaining=continue_remaining,
            maximum=maximum,
            confirmed=confirmed,
            environment={"TENDERCITE_LLM_API_KEY": KEY} if environment is None else environment,
            transport=httpx.MockTransport(handler or default_handler),
        )
    )


def test_one_request_default_reservation_wire_accounting_and_continuation(saved):
    corpus_data, directory, loaded = saved
    manifest, config, settings, steps, requests, _ = loaded
    calls = []

    def handler(request):
        calls.append(request)
        assert json.loads(request.content) == requests[0]["payload"]
        ledger = ExperimentLedger.existing(ledger_path())
        entry = ledger.data["entries"][-1]
        assert (
            entry["status"] == "RESERVED" and entry["staged_step"]["batch_id"] == steps[0].batch_id
        )
        assert entry["staged_step"]["plan_id"] == manifest["plan_id"]
        with pytest.raises(ValueError, match="locked"):
            with experiment_lock(ledger_path()):
                pass
        return httpx.Response(
            200, json=body_for(steps[0]), headers={"x-request-id": "req-staged-mock"}
        )

    first = invoke(saved, handler)
    assert first["api_calls_attempted"] == len(calls) == 1
    assert first["status"] == "COMPLETED_SELECTED_STEPS"
    before = copy.deepcopy(ExperimentLedger.existing(ledger_path()).data["entries"])
    second = invoke(saved, continue_remaining=True, output_name="continued-2")
    assert second["api_calls_attempted"] == 1
    ledger = ExperimentLedger.existing(ledger_path())
    assert ledger.data["entries"][:1] == before
    assert ledger.data["entries"][1]["staged_step"]["batch_id"] == steps[1].batch_id
    assert ledger.committed() == Decimal("0.00052")
    assert len({e["attempt_id"] for e in ledger.data["entries"]}) == 2
    assert KEY not in ledger_path().read_text()
    assert all(
        KEY not in p.read_text()
        for p in (external_benchmark.EXTERNAL_ROOT / "paid-mock-1").iterdir()
    )


def test_duplicate_paid_requests_rejected_and_old_pair_still_available(saved):
    invoke(saved)
    with pytest.raises(ValueError, match="already attempted"):
        invoke(saved, output_name="duplicate")
    corpus_data, _, loaded = saved
    _, config, _, _, _, _ = loaded
    questions = build_comparison_questions(corpus_data)[:2]
    report, prompts = preflight(config, corpus_data, questions, environment={})
    pair = asyncio.run(
        execute_pair(
            config,
            corpus_data,
            questions,
            report,
            prompts,
            external_benchmark.EXTERNAL_ROOT / "old-pair",
            confirmed=True,
            environment={"TENDERCITE_LLM_API_KEY": KEY},
            transport=httpx.MockTransport(
                lambda r: httpx.Response(200, json=completion(questions[0].question_id))
            ),
        )
    )
    assert pair["status"] == "COMPLETED_PAIR"
    ledger = ExperimentLedger.existing(ledger_path())
    assert len(ledger.data["entries"]) == 3
    with pytest.raises(ValueError, match="already attempted"):
        ledger.assert_pair_new(questions[0].question_id)


@pytest.mark.parametrize(
    "selection", ["too_many", "duplicate", "unknown", "empty", "conflicting", "unbounded"]
)
def test_paid_selection_is_explicit_bounded_and_validated_before_transmission(saved, selection):
    steps = saved[2][3]
    ids, maximum, continuing = [steps[0].batch_id], 1, False
    if selection == "too_many":
        ids = [s.batch_id for s in steps[:2]]
    elif selection == "duplicate":
        ids = [steps[0].batch_id, steps[0].batch_id]
        maximum = 2
    elif selection == "unknown":
        ids = ["batch-" + "f" * 24]
    elif selection == "empty":
        ids = []
    elif selection == "conflicting":
        continuing = True
    else:
        maximum = 4
    with pytest.raises(ValueError):
        invoke(
            saved,
            batch_ids=ids,
            maximum=maximum,
            continue_remaining=continuing,
            handler=lambda r: pytest.fail("Invalid selection made a paid call"),
        )
    assert not ExperimentLedger.existing(ledger_path()).data["entries"]


@pytest.mark.parametrize("missing", ["key", "transmission", "question", "ledger"])
def test_every_gate_required_and_staged_execution_never_creates_a_ledger(saved, missing):
    environment, confirmed, qid = {"TENDERCITE_LLM_API_KEY": KEY}, True, None
    if missing == "key":
        environment = {}
    elif missing == "transmission":
        confirmed = False
    elif missing == "question":
        qid = "wrong-question"
    else:
        ledger_path().unlink()
    with pytest.raises(ValueError):
        invoke(
            saved,
            confirmed=confirmed,
            environment=environment,
            question_id=qid,
            handler=lambda r: pytest.fail("Blocked execution sent a request"),
        )
    if missing == "ledger":
        assert not ledger_path().exists()
    else:
        assert not ExperimentLedger.existing(ledger_path()).data["entries"]


@pytest.mark.parametrize(
    "failure",
    [
        "length",
        "missing_usage",
        "http429",
        "bad_quote",
        "invalid_json",
        "wrong_model",
        "refusal",
        "timeout",
    ],
)
def test_failure_stops_selection_preserves_billable_cost_and_blocks_continuation(saved, failure):
    steps = saved[2][3]
    calls = []

    def handler(request):
        calls.append(request)
        body = body_for(steps[0])
        if failure == "length":
            body["choices"][0].update(finish_reason="length", message={"content": ""})
            body["usage"].update(completion_tokens=100, total_tokens=200)
            body["usage"]["completion_tokens_details"]["reasoning_tokens"] = 100
        elif failure == "missing_usage":
            body.pop("usage")
        elif failure == "http429":
            return httpx.Response(
                429,
                json={"error": {"code": "rate_limit_exceeded", "message": "Mock rate limit"}},
                headers={"x-request-id": "req-staged-failure"},
            )
        elif failure == "bad_quote":
            answer = extraction(steps[0], [finding(steps[0])]).model_dump()
            answer["findings"][0]["evidence"][0]["quote"] = "Fabricated source text"
            body["choices"][0]["message"]["content"] = json.dumps(answer)
        elif failure == "invalid_json":
            body["choices"][0]["message"]["content"] = "{"
        elif failure == "wrong_model":
            body["model"] = "unexpected-model"
        elif failure == "refusal":
            body["choices"][0]["message"]["refusal"] = "Mock provider refusal"
        else:
            raise httpx.ReadTimeout("Mock ambiguous billing")
        return httpx.Response(200, json=body)

    result = invoke(saved, handler, batch_ids=[s.batch_id for s in steps], maximum=3)
    assert result["status"] == "STOPPED" and result["api_calls_attempted"] == len(calls) == 1
    entry = ExperimentLedger.existing(ledger_path()).data["entries"][0]
    if failure in ("length", "bad_quote", "invalid_json", "refusal"):
        assert entry["status"] == "SETTLED" and Decimal(entry["usage_derived_cost"]) > 0
    else:
        assert entry["status"] == "UNCERTAIN" and "usage_derived_cost" not in entry
    with pytest.raises(ValueError, match="accounting|failed/incomplete"):
        invoke(saved, continue_remaining=True, output_name="unsafe-continue")


def test_shared_20_usd_budget_counts_existing_experiment_costs(saved):
    invoke(saved)
    ledger = ExperimentLedger.existing(ledger_path())
    original = ledger.data["entries"][0]
    # Another original synthetic settled attempt occupies the experiment budget.
    occupied = {
        "key": "f" * 64,
        "attempt_id": "original-budget-test",
        "question_id": "generated-multi",
        "strategy": "e5_rag",
        "status": "SETTLED",
        "reserved_cost": "19.999",
        "usage_derived_cost": "19.999",
        "output_dir": str(external_benchmark.EXTERNAL_ROOT / "old"),
    }
    ledger.data["entries"].append(occupied)
    atomic_save(ledger_path(), ledger.data)
    before = ledger_path().read_bytes()
    with pytest.raises(ValueError, match="budget"):
        invoke(saved, continue_remaining=True, output_name="over-budget")
    assert ledger_path().read_bytes() == before
    assert ExperimentLedger.existing(ledger_path()).data["entries"][0] == original


def test_output_crash_leaves_durable_reservation_and_cannot_recharge(saved, monkeypatch):
    original_write = execution.write_new_json

    def crash(path, value):
        if path.name.startswith("staged_extract"):
            raise OSError("Synthetic filesystem interruption")
        return original_write(path, value)

    monkeypatch.setattr(execution, "write_new_json", crash)
    with pytest.raises(OSError, match="interruption"):
        invoke(saved)
    entry = ExperimentLedger.existing(ledger_path()).data["entries"][0]
    assert entry["status"] == "RESERVED"
    with pytest.raises(ValueError, match="accounting"):
        invoke(saved, continue_remaining=True, output_name="no-recharge")


def test_empty_extraction_is_successful_and_missing_response_never_counts_as_empty(saved):
    step = saved[2][3][0]
    invoke(saved, lambda r: httpx.Response(200, json=body_for(step, empty=True)))
    corpus_data, _, loaded = saved
    manifest, config, settings, steps, _, _ = loaded
    ledger = ExperimentLedger.existing(ledger_path())
    attempts = extraction_attempts(ledger, manifest, steps, config, settings, corpus_data)
    assert attempts[step.batch_id]["completed"]
    assert consolidate(manifest, attempts)["no_relevant_findings_batches"] == [step.batch_id]
    entry = ledger.data["entries"][0]
    from pathlib import Path

    (Path(entry["output_dir"]) / f"{entry['strategy']}.json").unlink()
    attempts = extraction_attempts(ledger, manifest, steps, config, settings, corpus_data)
    assert not attempts[step.batch_id]["completed"]
    assert not consolidate(manifest, attempts)["no_relevant_findings_batches"]


def test_synthesis_requires_complete_valid_consolidation_and_is_single_paid_step(saved):
    corpus_data, plan_dir, loaded = saved
    manifest, config, settings, steps, _, plan_hash = loaded
    invoke(saved, batch_ids=[s.batch_id for s in steps], maximum=3)
    ledger = ExperimentLedger.existing(ledger_path())
    attempts = extraction_attempts(ledger, manifest, steps, config, settings, corpus_data)
    folder = external_benchmark.EXTERNAL_ROOT / "consolidated"
    write_new_json(folder / "consolidation.json", consolidate(manifest, attempts))
    before = copy.deepcopy(ledger.data["entries"])
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json=completion(manifest["question_id"]))

    def synth(output_name):
        return asyncio.run(
            execute_synthesis(
                manifest,
                config,
                settings,
                steps,
                corpus_data,
                plan_dir,
                plan_hash,
                folder,
                external_benchmark.EXTERNAL_ROOT / output_name,
                question_id=manifest["question_id"],
                confirmed=True,
                environment={"TENDERCITE_LLM_API_KEY": KEY},
                transport=httpx.MockTransport(handler),
            )
        )

    result = synth("synthesis-1")
    assert result["status"] == "COMPLETED_SELECTED_STEPS" and len(calls) == 1
    ledger = ExperimentLedger.existing(ledger_path())
    assert ledger.data["entries"][:3] == before
    assert ledger.data["entries"][-1]["staged_step"]["stage"] == "synthesis"
    with pytest.raises(ValueError, match="already attempted"):
        synth("duplicate-synthesis")
    assert len(calls) == 1


def test_cli_inventory_plan_and_default_extract_never_create_http_or_ledger(
    staged,  # noqa: F811
    inputs,  # noqa: F811
    monkeypatch,
):
    monkeypatch.setattr(
        httpx, "AsyncClient", lambda *a, **k: pytest.fail("Offline CLI attempted HTTP")
    )
    corpus_data, config, settings, manifest, steps, _ = staged
    root = external_benchmark.EXTERNAL_ROOT
    monkeypatch.setattr(cli, "load_comparison_corpus", lambda *a, **k: corpus_data)
    config_path, settings_path = root / "config.json", root / "stages.json"
    config_path.write_text(config.model_dump_json())
    settings_path.write_text(settings.model_dump_json())
    shared = [
        "--acquisition-dir",
        str(inputs["acquisition"]),
        "--retrieval-results",
        str(inputs["retrieval"]),
    ]
    inventory = root / "inventory"
    assert cli.main(["inventory", *shared, "--output-dir", str(inventory)]) == 0
    assert read_local_json(inventory / "source-inventory.json")[0]["page_count"] == 3
    directory = root / "offline-plan"
    assert (
        cli.main(
            [
                "plan",
                *shared,
                "--config",
                str(config_path),
                "--stage-config",
                str(settings_path),
                "--question-id",
                manifest["question_id"],
                "--output-dir",
                str(directory),
            ]
        )
        == 0
    )
    assert not ledger_path().exists()
    assert (
        cli.main(
            [
                "extract",
                *shared,
                "--plan-dir",
                str(directory),
                "--question-id",
                manifest["question_id"],
                "--batch-id",
                steps[0].batch_id,
                "--output-dir",
                str(root / "selected-dry"),
            ]
        )
        == 0
    )
    assert not ledger_path().exists()
    assert (
        cli.main(
            [
                "extract",
                *shared,
                "--execute",
                "--plan-dir",
                str(directory),
                "--question-id",
                manifest["question_id"],
                "--batch-id",
                steps[0].batch_id,
                "--output-dir",
                str(root / "blocked-execute"),
            ]
        )
        == 1
    )
    assert not ledger_path().exists()
