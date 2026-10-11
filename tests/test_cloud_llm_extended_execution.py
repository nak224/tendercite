"""Generated PDFs, artificial rates and MockTransport only; no paid requests."""
# ruff: noqa: F811 - shared pytest fixtures are intentionally injected by name.

import asyncio
import copy
import json
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
from test_cloud_llm_contexts import comparison, response  # noqa: F401
from test_cloud_llm_execution import (  # noqa: F401
    KEY,
    cli_arguments,
    completion,
    only_mock_http,
    openai_config,
    pilot,
)
from test_external_document_metrics import corpus  # noqa: F401
from test_external_qa_spans import context  # noqa: F401
from test_external_source_span_runner import inputs  # noqa: F401

from evaluation import cloud_llm_comparison as cli
from evaluation.cloud_llm_contexts import build_comparison_questions
from evaluation.cloud_llm_execution import execute_pair, response_record
from evaluation.cloud_llm_ledger import ExperimentLedger, atomic_save, experiment_lock, ledger_path
from evaluation.cloud_llm_preflight import ComparisonConfig, configured_from_environment, preflight
from tendercite.services.llm.openai_compatible import CompletionResponse


def completed_body(question):
    body = completion(question.question_id)
    body["choices"][0]["message"]["content"] = response(question).model_dump_json()
    return body


def truncated_body(question):
    body = completed_body(question)
    body["choices"][0].update(finish_reason="length")
    body["choices"][0]["message"]["content"] = '{"question_id":'
    # Deliberately below the configured cap: status, not equality, proves truncation.
    body["usage"].update(completion_tokens=100, total_tokens=200)
    return body


@pytest.fixture
def truncated(pilot):
    _, data, questions, _, _, output = pilot
    config = openai_config(max_output_tokens=4096)
    report, prompts = preflight(config, data, questions, environment={})
    calls = []

    def handler(request):
        body = completed_body(questions[0]) if not calls else truncated_body(questions[1])
        calls.append(request)
        return httpx.Response(
            200, json=body, headers={"x-request-id": f"req-original-{len(calls)}"}
        )

    result = asyncio.run(
        execute_pair(
            config,
            data,
            questions,
            report,
            prompts,
            output,
            confirmed=True,
            environment={"TENDERCITE_LLM_API_KEY": KEY},
            transport=httpx.MockTransport(handler),
        )
    )
    assert result["status"] == "STOPPED" and len(calls) == 2
    return config, data, questions, output, result


def retry(
    truncated,
    *,
    config=None,
    original_id=None,
    strategy="full_context",
    authorized=True,
    confirmed=True,
    key=True,
    handler=None,
    output=None,
):
    old, data, questions, directory, original = truncated
    config = config or openai_config(max_output_tokens=8192)
    selected = [q for q in questions if q.strategy == strategy]
    report, prompts = preflight(config, data, selected, environment={})
    calls = []

    def mocked(request):
        calls.append(request)
        return (
            handler(request) if handler else httpx.Response(200, json=completed_body(selected[0]))
        )

    result = asyncio.run(
        execute_pair(
            config,
            data,
            selected,
            report,
            prompts,
            output or directory.with_name("retry-full"),
            confirmed=confirmed,
            environment={"TENDERCITE_LLM_API_KEY": KEY} if key else {},
            retry_strategy=strategy,
            original_attempt_id=original_id or original["attempt_id"],
            retry_authorized=authorized,
            transport=httpx.MockTransport(mocked),
        )
    )
    return result, calls


@pytest.mark.parametrize("cap", [4096, 8192, 12288, 128000])
def test_documented_sol_caps_are_configurable_and_auditable(cap):
    config = openai_config(max_output_tokens=cap, usable_context_tokens=1_050_000)
    assert config.model_capabilities.max_completion_tokens == 128000
    assert config.model_capabilities.context_window_tokens == 1_050_000
    assert str(config.model_capabilities.as_of) == "2026-10-11"
    assert config.model_capabilities.documentation_url.endswith("/models/gpt-6.1-sol")
    assert config.model_dump(mode="json")["max_output_tokens"] == cap


def test_default_4096_and_unknown_model_limits_require_sourced_capabilities():
    raw = openai_config().model_dump(mode="json")
    raw.pop("max_output_tokens")
    assert ComparisonConfig.model_validate(raw).max_output_tokens == 4096
    from test_cloud_llm_preflight import configuration

    with pytest.raises(ValueError, match="documented model_capabilities"):
        configuration(max_output_tokens=8192)


@pytest.mark.parametrize("mutation", ["cap", "context", "model", "inflated_declaration"])
def test_unsupported_output_or_context_and_wrong_capabilities_fail(mutation):
    raw = openai_config(max_output_tokens=8192).model_dump(mode="json")
    if mutation == "cap":
        raw["max_output_tokens"] = 128001
    elif mutation == "context":
        raw["usable_context_tokens"] = 1_050_001
    elif mutation == "model":
        raw["model_capabilities"]["model"] = "another-model"
    else:
        raw["model_capabilities"]["max_completion_tokens"] = 999999
    with pytest.raises(ValueError):
        ComparisonConfig.model_validate(raw)


def test_larger_output_reprices_preflight_and_keeps_sources_prompts_and_estimator(truncated):
    old, data, questions, _, _ = truncated
    selected = [questions[1]]
    before, original = preflight(old, data, selected, environment={})
    config = configured_from_environment(
        old.model_dump(mode="json"), {"TENDERCITE_LLM_MAX_OUTPUT_TOKENS": "8192"}
    )
    after, expanded = preflight(config, data, selected, environment={})
    assert original[0]["payload"]["messages"] == expanded[0]["payload"]["messages"]
    assert original[0]["prepared"] == expanded[0]["prepared"]
    assert original[0]["payload"]["response_format"] == expanded[0]["payload"]["response_format"]
    assert expanded[0]["payload"]["max_completion_tokens"] == 8192
    br, ar = before["request_preflight"][0], after["request_preflight"][0]
    assert br["request_payload_sha256"] != ar["request_payload_sha256"]
    assert br["input_token_estimate"] == ar["input_token_estimate"]
    assert Decimal(ar["maximum_output_cost"]) == 2 * Decimal(br["maximum_output_cost"])
    assert old.pricing == config.pricing and old.spending == config.spending
    assert after["configuration"]["model_capabilities"]["max_completion_tokens"] == 128000
    limited = openai_config(
        max_output_tokens=8192, usable_context_tokens=ar["input_token_estimate"] + 8191
    )
    assert preflight(limited, data, selected, environment={})[0]["status"] == "BLOCKED_PREFLIGHT"


@pytest.mark.parametrize(
    "failure,expected",
    [
        ("length", "OUTPUT_TOKEN_TRUNCATION"),
        ("refusal", "PROVIDER_REFUSAL"),
        ("invalid_json", "INVALID_STRUCTURED_JSON"),
        ("invalid_schema", "INVALID_JSON_SCHEMA"),
        ("structure", "INVALID_RESPONSE_STRUCTURE"),
        ("http", "PROVIDER_HTTP_ERROR"),
    ],
)
def test_generation_diagnostics_are_separate_from_accounting(pilot, failure, expected):
    config, data, questions, _, _, _ = pilot
    body = completion(questions[0].question_id)
    choice = body["choices"][0]
    status = 200
    if failure == "length":
        choice["finish_reason"] = "length"
    elif failure == "refusal":
        choice["message"]["refusal"] = "Synthetic refusal"
    elif failure == "invalid_json":
        choice["message"]["content"] = "{"
    elif failure == "invalid_schema":
        choice["message"]["content"] = '{"question_id":"synthetic"}'
    elif failure == "structure":
        body["choices"] = []
    else:
        body = {"error": {"code": "synthetic-http-error", "message": "Original mock error"}}
        status = 429
    record, cost = response_record(
        CompletionResponse(json.dumps(body).encode(), status, "req-mock", 0.1),
        questions[0],
        data,
        config,
    )
    assert record["generation_status"] == expected
    assert record["response_validity"] == "INVALID_OR_INCOMPLETE"
    assert (cost is None) == (failure == "http")


def test_missing_usage_retains_provider_truncation_status(pilot):
    config, data, questions, _, _, _ = pilot
    body = truncated_body(questions[1])
    body.pop("usage")
    record, cost = response_record(
        CompletionResponse(json.dumps(body).encode(), 200, "req-mock", 0.1),
        questions[1],
        data,
        config,
    )
    assert record["generation_status"] == "OUTPUT_TOKEN_TRUNCATION"
    assert record["completion_status"] == "length" and cost is None
    assert record["accounting_status"] == "UNCERTAIN"


def test_truncation_retry_sends_only_full_and_preserves_all_history(truncated):
    _, _, _, output, original = truncated
    originals = {p.name: p.read_bytes() for p in output.iterdir()}
    before = copy.deepcopy(ExperimentLedger.existing(ledger_path()).data["entries"])
    result, calls = retry(truncated)
    assert result["status"] == "COMPLETED_STRATEGY_RETRY" and len(calls) == 1
    assert result["attempt_id"] != original["attempt_id"]
    assert json.loads(calls[0].content)["max_completion_tokens"] == 8192
    ledger = ExperimentLedger.existing(ledger_path())
    assert ledger.data["entries"][:2] == before
    assert ledger.data["entries"][2]["retry_of_key"] == before[1]["key"]
    assert ledger.data["entries"][2]["retry_of_attempt"] == original["attempt_id"]
    assert ledger.data["entries"][2]["key"] not in {e["key"] for e in before}
    assert originals == {p.name: p.read_bytes() for p in output.iterdir()}
    assert ledger.committed() == sum(
        Decimal(e["usage_derived_cost"]) for e in ledger.data["entries"]
    )
    with pytest.raises(ValueError, match="subsequent attempt"):
        retry(truncated, output=output.with_name("duplicate-retry"))
    with pytest.raises(ValueError, match="metadata mismatch"):
        retry(
            truncated,
            config=openai_config(max_output_tokens=12288),
            original_id=result["attempt_id"],
            output=output.with_name("valid-repeat"),
        )


@pytest.mark.parametrize(
    "failure",
    [
        "success",
        "wrong_attempt",
        "uncertain",
        "other_error",
        "tampered",
        "same_cap",
        "pricing",
        "authorization",
        "key",
        "transmission",
    ],
)
def test_truncation_retry_rejections_send_no_request_and_preserve_history(truncated, failure):
    config, data, questions, output, original = truncated
    kwargs = {"handler": lambda r: pytest.fail("Rejected retry sent HTTP")}
    ledger = ExperimentLedger.existing(ledger_path())
    if failure == "success":
        kwargs["strategy"] = "e5_rag"
    elif failure == "wrong_attempt":
        kwargs["original_id"] = "unrelated-attempt"
    elif failure == "uncertain":
        ledger.data["entries"][1]["status"] = "UNCERTAIN"
        atomic_save(ledger_path(), ledger.data)
    elif failure in ("other_error", "tampered"):
        path = output / "full_context.json"
        record = json.loads(path.read_text())
        record["completion_status"] = "stop"
        path.write_text(json.dumps(record))
    elif failure == "same_cap":
        kwargs["config"] = config
    elif failure == "pricing":
        raw = config.model_dump(mode="json")
        raw["max_output_tokens"] = 8192
        raw["pricing"]["output_per_million"] = "4"
        kwargs["config"] = ComparisonConfig.model_validate(raw)
    elif failure == "authorization":
        kwargs["authorized"] = False
    elif failure == "key":
        kwargs["key"] = False
    else:
        kwargs["confirmed"] = False
    before = ledger_path().read_bytes()
    with pytest.raises(ValueError):
        retry(truncated, **kwargs)
    assert ledger_path().read_bytes() == before


def test_truncation_retry_lock_and_cumulative_budget_block_before_sending(truncated):
    _, data, _, output, _ = truncated
    with experiment_lock(ledger_path()):
        with pytest.raises(ValueError, match="locked"):
            retry(truncated)
    ledger = ExperimentLedger.existing(ledger_path())
    other = build_comparison_questions(data)[2]
    charged = ledger.reserve(other, "19.99", output.with_name("other"))
    ledger.reconcile(charged, {"usage_derived_cost": "19.99"})
    with pytest.raises(ValueError, match="remaining persistent"):
        retry(truncated, handler=lambda r: pytest.fail("Overspending sent HTTP"))


def test_dry_run_and_cli_single_retry_require_explicit_gates(truncated, cli_arguments, monkeypatch):
    old, _, _, output, original = truncated
    raw = old.model_dump(mode="json")
    raw["max_output_tokens"] = 8192
    Path(cli_arguments[1]).write_text(json.dumps(raw))
    args = cli_arguments + [
        "--strategy",
        "full_context",
        "--retry-truncated-attempt",
        original["attempt_id"],
    ]
    args[7] = str(output.with_name("larger-dry-run"))
    assert cli.main(args) == 0
    dry = json.loads((Path(args[7]) / "preflight.json").read_text())
    assert dry["api_calls"] == 0 and dry["planned_requests"] == 1
    assert dry["truncation_retry"]["original_attempt_id"] == original["attempt_id"]
    execute = cli.execute_pair
    calls = []

    async def mocked(*a, **k):
        return await execute(
            *a,
            **k,
            transport=httpx.MockTransport(
                lambda r: (
                    calls.append(r) or httpx.Response(200, json=completed_body(truncated[2][1]))
                )
            ),
        )

    monkeypatch.setattr(cli, "execute_pair", mocked)
    monkeypatch.setenv("TENDERCITE_LLM_API_KEY", KEY)
    args[7] = str(output.with_name("unauthorized-single"))
    assert cli.main(args + ["--execute", "--confirm-transmission"]) == 1
    assert not calls
    args[7] = str(output.with_name("authorized-single"))
    assert (
        cli.main(args + ["--execute", "--confirm-transmission", "--authorize-truncated-retry"]) == 0
    )
    assert len(calls) == 1


def test_settled_legacy_record_can_retry_without_rewriting_old_history(truncated):
    import hashlib

    ledger = ExperimentLedger.existing(ledger_path())
    for entry in ledger.data["entries"]:
        entry.pop("attempt_id")
        entry.pop("response_file_sha256")
        entry["key"] = hashlib.sha256(
            f"{entry['question_id']}:{entry['strategy']}".encode()
        ).hexdigest()
        path = Path(entry["output_dir"]) / f"{entry['strategy']}.json"
        record = json.loads(path.read_text())
        record.pop("attempt_id")
        record["configuration"].pop("model_capabilities")
        record["reservation_key"] = entry["key"]
        path.write_text(json.dumps(record))
    atomic_save(ledger_path(), ledger.data)
    original = copy.deepcopy(ledger.data["entries"])
    parent = ledger.attempt_id(original[1])
    result, calls = retry(truncated, original_id=parent)
    assert len(calls) == 1 and result["retry"]["original_attempt_id"] == parent
    assert ExperimentLedger.existing(ledger_path()).data["entries"][:2] == original


def test_non_json_http_error_stays_a_provider_http_diagnostic(pilot):
    config, data, questions, _, _, _ = pilot
    record, cost = response_record(
        CompletionResponse(b"Synthetic gateway unavailable", 502, "req-gateway", 0.1),
        questions[0],
        data,
        config,
    )
    assert record["generation_status"] == "PROVIDER_HTTP_ERROR"
    assert record["primary_cause"] == "http_error" and record["http_status"] == 502
    assert record["accounting_status"] == "UNCERTAIN" and cost is None
