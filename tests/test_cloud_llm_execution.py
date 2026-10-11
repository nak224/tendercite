"""Original synthetic PDFs, artificial prices and MockTransport only: never paid calls."""

import asyncio
import copy
import json
import subprocess
import sys
from decimal import Decimal

import httpx
import pytest
from test_cloud_llm_contexts import comparison, response  # noqa: F401
from test_cloud_llm_preflight import configuration
from test_external_document_metrics import corpus  # noqa: F401
from test_external_qa_spans import context  # noqa: F401
from test_external_source_span_runner import inputs  # noqa: F401

from evaluation import cloud_llm_comparison as cli
from evaluation import cloud_llm_execution as execution
from evaluation import external_benchmark
from evaluation.cloud_llm_contexts import QAAnswer, build_comparison_questions, validate_answer
from evaluation.cloud_llm_execution import execute_pair, validate_execution
from evaluation.cloud_llm_ledger import ExperimentLedger, experiment_lock, ledger_path
from evaluation.cloud_llm_preflight import preflight, prepared_payload
from evaluation.cloud_llm_usage import account_usage
from tendercite.services.llm.openai_compatible import OpenAICompatibleLLM

KEY = "private-mocked-api-key-marker"


def openai_config(**changes):
    # Deliberately invented unit-test rates, not gpt-6.1-sol market prices.
    original = configuration().model_dump(mode="json")
    original.update(
        base_url="https://api.openai.com/v1",
        model="gpt-6.1-sol",
        spending={"currency": "USD", "per_request": "20", "cumulative": "20"},
    )
    original["pricing"].update(
        base_url=original["base_url"], model=original["model"], currency="USD"
    )
    original.update(changes)
    from evaluation.cloud_llm_preflight import ComparisonConfig

    return ComparisonConfig.model_validate(original)


@pytest.fixture(autouse=True)
def only_mock_http(monkeypatch):
    original = httpx.AsyncClient

    def guarded(*args, **kwargs):
        assert isinstance(kwargs.get("transport"), httpx.MockTransport), "Live HTTP forbidden"
        return original(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", guarded)


@pytest.fixture
def pilot(comparison):  # noqa: F811 - original synthetic PDF fixture.
    config = openai_config()
    questions = build_comparison_questions(comparison)[:2]
    report, prompts = preflight(config, comparison, questions, environment={})
    output = external_benchmark.EXTERNAL_ROOT / "pilot-output"
    return config, comparison, questions, report, prompts, output


def completion(question_id):
    return {
        "id": "chatcmpl-original-mock",
        "model": "gpt-6.1-sol",
        "choices": [
            {
                "finish_reason": "stop",
                "message": {
                    "content": json.dumps(
                        {
                            "question_id": question_id,
                            "status": "UNABLE_TO_ANSWER",
                            "answer": "",
                            "evidence": [],
                            "inability_reason": "Original mocked inability response.",
                        }
                    )
                },
            }
        ],
        "usage": {
            "prompt_tokens": 100,
            "completion_tokens": 20,
            "total_tokens": 120,
            "prompt_tokens_details": {"cached_tokens": 40, "cache_write_tokens": 10},
            "completion_tokens_details": {"reasoning_tokens": 6},
        },
    }


def invoke(pilot_data, handler, *, confirmed=True, environment=None):
    config, corpus_data, questions, report, prompts, output = pilot_data
    return asyncio.run(
        execute_pair(
            config,
            corpus_data,
            questions,
            report,
            prompts,
            output,
            confirmed=confirmed,
            environment={"TENDERCITE_LLM_API_KEY": KEY} if environment is None else environment,
            transport=httpx.MockTransport(handler),
        )
    )


def test_sol_payload_strict_nullable_fields_and_other_providers_remain_compatible(pilot):
    config, _, questions, _, prompts, _ = pilot
    payload = prompts[0]["payload"]
    assert payload == prepared_payload(config, questions[0])
    assert payload["max_completion_tokens"] == config.max_output_tokens
    assert payload["reasoning_effort"] == "low"
    assert not {"max_tokens", "temperature", "top_p"} & payload.keys()
    schema_info = payload["response_format"]["json_schema"]
    assert schema_info["strict"] is True
    schema = schema_info["schema"]
    assert "inability_reason" in schema["required"]
    assert "chunk_id" in schema["$defs"]["QACitation"]["required"]

    def check(node):
        if isinstance(node, dict):
            assert "default" not in node
            if node.get("type") == "object":
                assert node["additionalProperties"] is False
                assert set(node["required"]) == set(node["properties"])
            for value in node.values():
                check(value)
        elif isinstance(node, list):
            for value in node:
                check(value)

    check(schema)
    adapter = OpenAICompatibleLLM(base_url="https://other.invalid/v1", model="other")
    other = adapter.build_payload(**questions[0].prompts(), response_model=QAAnswer)
    assert other["temperature"] == 0 and "max_completion_tokens" not in other
    assert other["response_format"]["json_schema"]["schema"] == QAAnswer.model_json_schema()
    assert "strict" not in other["response_format"]["json_schema"]


@pytest.mark.parametrize("chunk", [None, "", "unknown"])
def test_rag_citations_require_actual_supplied_chunk_ids(pilot, chunk):
    _, corpus_data, questions, _, _, _ = pilot
    answer = response(questions[0])
    answer.evidence[0].chunk_id = chunk
    assert validate_answer(answer, questions[0], corpus_data)["citation_status"] == "INVALID_QUOTE"


def test_inability_and_answer_without_evidence_are_distinct(pilot):
    _, corpus_data, questions, _, _, _ = pilot
    answered = response(questions[0], [])
    unable = QAAnswer.model_validate_json(
        completion(questions[0].question_id)["choices"][0]["message"]["content"]
    )
    assert (
        validate_answer(answered, questions[0], corpus_data)["response_classification"]
        == "ANSWERED_MISSING_EVIDENCE"
    )
    assert (
        validate_answer(unable, questions[0], corpus_data)["response_classification"]
        == "UNABLE_TO_ANSWER"
    )


def test_pair_uses_exact_preflight_wire_and_durable_reservations_with_no_key_leak(pilot):
    config, _, questions, _, prompts, output = pilot
    requests = []

    def handler(request):
        index = len(requests)
        requests.append(request)
        assert json.loads(request.content) == prompts[index]["payload"]
        assert request.url.path == "/v1/chat/completions"
        assert request.headers["Authorization"] == f"Bearer {KEY}"
        state = json.loads(ledger_path().read_text())
        assert state["entries"][-1]["status"] == "RESERVED"
        assert Decimal(state["entries"][-1]["reserved_cost"]) > 0
        assert request.extensions["timeout"]["read"] == config.request_timeout_seconds
        return httpx.Response(
            200, json=completion(questions[0].question_id), headers={"x-request-id": "req-mock"}
        )

    result = invoke(pilot, handler)
    assert len(requests) == 2 and result["status"] == "COMPLETED_PAIR"
    assert result["accounting_complete"] and result["api_calls_attempted"] == 2
    assert Decimal(result["usage_derived_run_cost"]) == Decimal("0.00052")
    for strategy in ("e5_rag", "full_context"):
        data = json.loads((output / f"{strategy}.json").read_text())
        assert data["provider_request_id"] == "req-mock" and data["latency_seconds"] >= 0
        assert data["accounting"]["reasoning_tokens"] == 6
        assert data["citation_validation"]["response_classification"] == "UNABLE_TO_ANSWER"
    assert all(KEY not in p.read_text() for p in output.iterdir())
    assert KEY not in ledger_path().read_text()
    assert {e["status"] for e in json.loads(ledger_path().read_text())["entries"]} == {"SETTLED"}


def test_reasoning_is_not_double_counted_and_explicit_cache_tiers_are_applied():
    config = openai_config(
        usage_pricing={
            "source": "Original artificial tier assumptions",
            "as_of": "2026-01-01",
            "tiers": [
                {
                    "above_prompt_tokens": 50,
                    "input_per_million": "1",
                    "output_per_million": "2",
                    "cached_input_per_million": "0.5",
                    "cache_write_input_per_million": "1.5",
                }
            ],
        }
    )
    accounting = account_usage(completion("original-test")["usage"], config)
    assert Decimal(accounting["usage_derived_cost"]) == Decimal("0.000125")
    assert accounting["visible_completion_tokens"] == 14
    assert accounting["tier_above_prompt_tokens"] == 50
    assert accounting["final_provider_billing"] == "REQUIRES_DASHBOARD_RECONCILIATION"
    usage = {"prompt_tokens": 50, "completion_tokens": 10, "total_tokens": 60}
    assert account_usage(usage, config)["tier_above_prompt_tokens"] is None
    assert account_usage(usage, config)["cached_input_tokens"] is None


@pytest.mark.parametrize(
    "failure", ["missing", "incomplete", "total", "reasoning", "cached", "boolean", "audio"]
)
def test_missing_or_inconsistent_usage_holds_reservation_and_stops(pilot, failure):
    body = completion(pilot[2][0].question_id)
    usage = body["usage"]
    if failure == "missing":
        body.pop("usage")
    elif failure == "incomplete":
        usage.pop("completion_tokens")
    elif failure == "total":
        usage["total_tokens"] = 121
    elif failure == "reasoning":
        usage["completion_tokens_details"]["reasoning_tokens"] = 21
    elif failure == "cached":
        usage["prompt_tokens_details"]["cached_tokens"] = 101
    elif failure == "boolean":
        usage["prompt_tokens"] = True
    else:
        usage["prompt_tokens_details"]["audio_tokens"] = 1
    calls = []
    result = invoke(pilot, lambda r: calls.append(r) or httpx.Response(200, json=body))
    assert len(calls) == 1 and result["status"] == "STOPPED"
    assert not result["accounting_complete"] and result["usage_derived_run_cost"] is None
    entries = json.loads(ledger_path().read_text())["entries"]
    assert entries[0]["status"] == "UNCERTAIN"
    assert Decimal(result["persistent_committed_cost_or_reservations"]) == Decimal(
        entries[0]["reserved_cost"]
    )


@pytest.mark.parametrize("status", [400, 401, 429, 500])
def test_http_error_preserves_original_body_and_never_retries(pilot, status):
    body = {"error": {"message": f"Original mock provider error {KEY}", "code": "test-error"}}
    calls = []
    result = invoke(
        pilot,
        lambda r: (
            calls.append(r)
            or httpx.Response(status, json=body, headers={"x-request-id": "req-error"})
        ),
    )
    assert result["status"] == "STOPPED" and len(calls) == 1
    text = (pilot[-1] / "e5_rag.json").read_text()
    assert KEY not in text and "Original mock provider error [REDACTED]" in text
    assert json.loads(text)["http_status"] == status


@pytest.mark.parametrize(
    "failure", ["length", "refusal", "invalid_json", "wrong_model", "missing_model"]
)
def test_invalid_or_truncated_completions_stop_without_model_or_schema_fallback(pilot, failure):
    body = completion(pilot[2][0].question_id)
    choice = body["choices"][0]
    if failure == "length":
        choice["finish_reason"] = "length"
    elif failure == "refusal":
        choice["message"]["refusal"] = "Original mocked refusal."
    elif failure == "invalid_json":
        choice["message"]["content"] = "not-json"
    elif failure == "wrong_model":
        body["model"] = "other-model"
    else:
        body.pop("model")
    calls = []
    result = invoke(pilot, lambda r: calls.append(r) or httpx.Response(200, json=body))
    assert result["status"] == "STOPPED" and len(calls) == 1
    record = json.loads((pilot[-1] / "e5_rag.json").read_text())
    assert record["response_validity"] == "INVALID_OR_INCOMPLETE"
    assert record["raw_response"]
    assert record["accounting_status"] == (
        "UNCERTAIN" if failure in ("wrong_model", "missing_model") else "USAGE_RECORDED_NOT_INVOICE"
    )


def test_transport_timeout_is_uncertain_and_no_retry_occurs(pilot):
    calls = []

    def handler(request):
        calls.append(request)
        raise httpx.ReadTimeout("Original mocked ambiguous read timeout")

    result = invoke(pilot, handler)
    assert result["api_calls_attempted"] == 1 and len(calls) == 1
    assert result["results"][0]["ledger_status"] == "UNCERTAIN"


@pytest.mark.parametrize(
    "failure",
    [
        "credentials",
        "permission",
        "selection",
        "preflight",
        "repetitions",
        "endpoint",
        "budget_currency",
        "key_control",
    ],
)
def test_execution_prerequisites_reject_before_http(pilot, failure):
    config, _, questions, report, _, _ = pilot
    confirmed, environment = True, {"TENDERCITE_LLM_API_KEY": KEY}
    if failure == "credentials":
        environment = {}
    elif failure == "permission":
        confirmed = False
    elif failure == "selection":
        questions = questions[:1]
    elif failure == "preflight":
        report["status"] = "BLOCKED_PREFLIGHT"
    elif failure == "repetitions":
        config.repetitions = 2
    elif failure == "endpoint":
        config.base_url = "https://third-party.invalid/v1"
    elif failure == "budget_currency":
        config.spending.currency = "EUR"
    else:
        environment["TENDERCITE_LLM_API_KEY"] = "key\nheader-injection"
    with pytest.raises(ValueError, match="Execution blocked"):
        validate_execution(config, report, questions, confirmed=confirmed, environment=environment)
    assert not ledger_path().exists()


def test_duplicate_execution_and_existing_output_preservation(pilot):
    def handler(request):
        return httpx.Response(200, json=completion(pilot[2][0].question_id))

    invoke(pilot, handler)
    original = (pilot[-1] / "e5_rag.json").read_bytes()
    with pytest.raises(ValueError, match="Existing response"):
        invoke(pilot, lambda r: pytest.fail("Repeated HTTP"))
    assert (pilot[-1] / "e5_rag.json").read_bytes() == original
    fresh = (*pilot[:-1], pilot[-1].with_name("second-invocation"))
    with pytest.raises(ValueError, match="already attempted"):
        invoke(fresh, lambda r: pytest.fail("Duplicate HTTP"))


def test_interruption_leaves_durable_reservation_and_blocks_another_question(pilot):
    def handler(request):
        assert json.loads(ledger_path().read_text())["entries"][-1]["status"] == "RESERVED"
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        invoke(pilot, handler)
    config, corpus_data, _, _, _, output = pilot
    other_questions = build_comparison_questions(corpus_data)[2:]
    report, prompts = preflight(config, corpus_data, other_questions, environment={})
    other = (
        config,
        corpus_data,
        other_questions,
        report,
        prompts,
        output.with_name("after-interrupt"),
    )
    with pytest.raises(ValueError, match="interrupted"):
        invoke(other, lambda r: pytest.fail("HTTP after ambiguous interruption"))


def test_persistent_spend_prevents_another_pair_and_cannot_be_reset_by_new_output(pilot):
    config, corpus_data, questions, _, _, output = pilot
    with experiment_lock(ledger_path()):
        ledger = ExperimentLedger(ledger_path(), config, corpus_data)
        entry = ledger.reserve(questions[0], "19.9999", output)
        ledger.reconcile(entry, {"usage_derived_cost": "19.9999"})
    other_questions = build_comparison_questions(corpus_data)[2:]
    report, prompts = preflight(config, corpus_data, other_questions, environment={})
    other = config, corpus_data, other_questions, report, prompts, output.with_name("over-budget")
    with pytest.raises(ValueError, match="remaining persistent"):
        invoke(other, lambda r: pytest.fail("Over-budget HTTP"))


def test_usage_exceeding_reservation_reconciles_known_cost_but_stops(pilot):
    body = completion(pilot[2][0].question_id)
    body["usage"].update(prompt_tokens=100_000, total_tokens=100_020)
    result = invoke(pilot, lambda r: httpx.Response(200, json=body))
    assert result["api_calls_attempted"] == 1
    assert result["results"][0]["ledger_status"] == "OVER_ESTIMATE"
    assert result["accounting_complete"]


def test_concurrent_ledger_lock_is_rejected_and_released(pilot):
    with experiment_lock(ledger_path()):
        with pytest.raises(ValueError, match="locked"):
            with experiment_lock(ledger_path()):
                pytest.fail("Second executor obtained ledger lock")
    with experiment_lock(ledger_path()):
        pass


def test_changed_pricing_cannot_reset_ledger_and_higher_usage_rates_are_blocked(pilot):
    config, corpus_data, questions, report, _, _ = pilot
    with experiment_lock(ledger_path()):
        ExperimentLedger(ledger_path(), config, corpus_data)
    modified = copy.deepcopy(config)
    modified.pricing.input_per_million = Decimal("1")
    with pytest.raises(ValueError, match="binding changed"):
        ExperimentLedger(ledger_path(), modified, corpus_data)
    expensive = openai_config(
        usage_pricing={
            "source": "Artificial incompatible tier",
            "as_of": "2026-01-01",
            "cache_write_input_per_million": "999",
        }
    )
    with pytest.raises(ValueError, match="unchanged reservation"):
        validate_execution(
            expensive,
            report,
            questions,
            confirmed=True,
            environment={"TENDERCITE_LLM_API_KEY": KEY},
        )


def test_independent_process_cannot_obtain_active_ledger_lock(pilot):
    script = (
        "import sys; from pathlib import Path; "
        "from evaluation import external_benchmark as e; "
        "from evaluation.cloud_llm_ledger import experiment_lock; "
        "e.EXTERNAL_ROOT=Path(sys.argv[1]); "
        "\ntry:\n with experiment_lock(Path(sys.argv[2])): sys.exit(2)"
        "\nexcept ValueError as exc:\n print(str(exc)); sys.exit(0)"
    )
    with experiment_lock(ledger_path()):
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                script,
                str(external_benchmark.EXTERNAL_ROOT),
                str(ledger_path()),
            ],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    assert result.returncode == 0 and "locked" in result.stdout


def test_response_output_failure_keeps_reservation_and_blocks_further_http(pilot, monkeypatch):
    def fail_output(*args):
        raise OSError("Original mocked disk failure")

    monkeypatch.setattr(execution, "write_new_json", fail_output)
    with pytest.raises(OSError, match="disk failure"):
        invoke(pilot, lambda r: httpx.Response(200, json=completion(pilot[2][0].question_id)))
    assert json.loads(ledger_path().read_text())["entries"][0]["status"] == "RESERVED"
    fresh = (*pilot[:-1], pilot[-1].with_name("after-write-failure"))
    with pytest.raises(ValueError, match="interrupted"):
        invoke(fresh, lambda r: pytest.fail("HTTP after failed persistence"))


def test_oversized_response_is_incomplete_and_retains_reservation(pilot):
    result = invoke(pilot, lambda r: httpx.Response(200, content=b"x" * 2_000_001))
    assert result["api_calls_attempted"] == 1 and not result["accounting_complete"]
    record = json.loads((pilot[-1] / "e5_rag.json").read_text())
    assert record["raw_response_truncated"] and len(record["raw_response"]) == 2_000_000


@pytest.mark.parametrize("bad", [[], {"version": "wrong"}, "not-json"])
def test_malformed_ledger_never_reinitializes_the_budget(pilot, bad):
    path = ledger_path()
    path.parent.mkdir(parents=True)
    raw = bad if isinstance(bad, str) else json.dumps(bad)
    path.write_text(raw)
    with pytest.raises(ValueError):
        invoke(pilot, lambda r: pytest.fail("HTTP with malformed ledger"))
    assert path.read_text() == raw


@pytest.fixture
def cli_arguments(pilot, monkeypatch):
    config, corpus_data, questions, _, _, output = pilot
    for variable in (
        "TENDERCITE_LLM_BASE_URL",
        "TENDERCITE_LLM_MODEL",
        "TENDERCITE_LLM_TEMPERATURE",
        "TENDERCITE_LLM_MAX_OUTPUT_TOKENS",
        "TENDERCITE_LLM_REQUEST_TIMEOUT_SECONDS",
    ):
        monkeypatch.delenv(variable, raising=False)
    monkeypatch.delenv("TENDERCITE_LLM_API_KEY", raising=False)
    monkeypatch.setattr(cli, "load_comparison_corpus", lambda *a: corpus_data)
    root = external_benchmark.EXTERNAL_ROOT
    config_path = root / "original-unit-config.json"
    config_path.write_text(config.model_dump_json())
    return [
        "--config",
        str(config_path),
        "--acquisition-dir",
        str(root / "acquisition"),
        "--retrieval-results",
        str(root / "recorded.json"),
        "--output-dir",
        str(output),
        "--question-id",
        questions[0].question_id,
    ]


def test_official_model_default_cli_still_never_creates_an_http_client(cli_arguments, monkeypatch):
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **k: pytest.fail("Default CLI paid call"))
    assert cli.main(cli_arguments) == 0
    output = cli_arguments[7]
    from pathlib import Path

    report = json.loads((Path(output) / "preflight.json").read_text())
    assert report["planned_requests"] == 2 and report["api_calls"] == 0
    assert not ledger_path().exists()


@pytest.mark.parametrize("missing", ["key", "permission", "question"])
def test_cli_requires_every_execution_gate(cli_arguments, monkeypatch, missing):
    monkeypatch.setenv("TENDERCITE_LLM_API_KEY", KEY)
    args = cli_arguments + ["--execute", "--confirm-transmission"]
    if missing == "key":
        monkeypatch.delenv("TENDERCITE_LLM_API_KEY")
    elif missing == "permission":
        args.remove("--confirm-transmission")
    else:
        del args[8:10]
    assert cli.main(args) == 1
    assert not ledger_path().exists()


def test_actual_cli_executes_exactly_one_mocked_pair_and_preserves_inputs(
    cli_arguments, pilot, monkeypatch, capsys
):
    original = cli.execute_pair
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json=completion(pilot[2][0].question_id))

    async def mocked(*args, **kwargs):
        return await original(*args, **kwargs, transport=httpx.MockTransport(handler))

    monkeypatch.setattr(cli, "execute_pair", mocked)
    monkeypatch.setenv("TENDERCITE_LLM_API_KEY", KEY)
    arguments = cli_arguments + ["--execute", "--confirm-transmission"]
    assert cli.main(arguments) == 0
    assert len(calls) == 2 and KEY not in capsys.readouterr().out
    from pathlib import Path

    output = Path(cli_arguments[7])
    preflight_data = json.loads((output / "preflight.json").read_text())
    assert preflight_data["planned_requests"] == 2 and preflight_data["adapter_sha256"]
    assert (output / "prompts.json").exists() and (output / "execution.json").exists()
    assert cli.main(arguments) == 1
    assert len(calls) == 2


def test_previous_dry_run_retains_original_estimates_and_rejects_changed_assumptions(
    cli_arguments, pilot
):
    from pathlib import Path

    baseline = external_benchmark.EXTERNAL_ROOT / "baseline-original-fixture.json"
    data = copy.deepcopy(pilot[3])
    baseline.write_text(json.dumps(data))
    assert cli.main(cli_arguments + ["--previous-preflight", str(baseline)]) == 0
    output = Path(cli_arguments[7])
    record = json.loads((output / "preflight.json").read_text())
    assert (
        record["previous_dry_run"]["estimated_cost_with_margin"]
        == data["estimated_experiment_cost_with_margin"]
    )
    assert record["previous_dry_run"]["sha256"]
    data["configuration"]["token_safety_multiplier"] = "1.5"
    baseline.write_text(json.dumps(data))
    cli_arguments[7] = str(output.with_name("changed-baseline"))
    assert cli.main(cli_arguments + ["--previous-preflight", str(baseline)]) == 1


@pytest.mark.parametrize("bad", [-1, "100", False, None, float("nan")])
def test_invalid_usage_counts_are_never_priced_as_complete(bad):
    usage = completion("original-test")["usage"]
    usage["prompt_tokens"] = bad
    with pytest.raises(ValueError, match="usage count"):
        account_usage(usage, openai_config())


def test_conflicting_cache_writes_and_malformed_detail_objects_are_uncertain():
    usage = completion("original-test")["usage"]
    usage["cache_creation_input_tokens"] = 11
    with pytest.raises(ValueError, match="Conflicting"):
        account_usage(usage, openai_config())
    usage = completion("original-test")["usage"]
    usage["prompt_tokens_details"] = False
    with pytest.raises(ValueError, match="Malformed"):
        account_usage(usage, openai_config())


def test_completion_above_configured_cap_is_recorded_and_stops(pilot):
    body = completion(pilot[2][0].question_id)
    body["usage"].update(completion_tokens=101, total_tokens=201)
    result = invoke(pilot, lambda r: httpx.Response(200, json=body))
    assert result["api_calls_attempted"] == 1 and result["accounting_complete"]
    record = json.loads((pilot[-1] / "e5_rag.json").read_text())
    assert "exceeds configured output-token limit" in record["error"]


def test_native_citations_cannot_replace_supplied_chunk_validation(pilot):
    body = completion(pilot[2][0].question_id)
    answer = response(pilot[2][0])
    answer.evidence[0].chunk_id = None
    message = body["choices"][0]["message"]
    message["content"] = answer.model_dump_json()
    message["citations"] = [{"provider_claim": "verified mock citation"}]
    result = invoke(pilot, lambda r: httpx.Response(200, json=body))
    assert result["status"] == "COMPLETED_PAIR"
    record = json.loads((pilot[-1] / "e5_rag.json").read_text())
    assert record["provider_native_metadata"]["citations"] == message["citations"]
    assert record["citation_validation"]["citation_status"] == "INVALID_QUOTE"
    assert record["citation_validation"]["semantic_correctness"] == "NOT_INDEPENDENTLY_ESTABLISHED"
