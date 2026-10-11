"""Artificial pricing/model assumptions for deterministic tests, never provider prices."""

import asyncio
import json
from decimal import ROUND_CEILING, Decimal

import httpx
import pytest
from test_cloud_llm_contexts import comparison  # noqa: F401
from test_external_document_metrics import corpus  # noqa: F401
from test_external_qa_spans import context  # noqa: F401
from test_external_source_span_runner import inputs  # noqa: F401

from evaluation.cloud_llm_contexts import QAAnswer, build_comparison_questions
from evaluation.cloud_llm_preflight import (
    ComparisonConfig,
    configured_from_environment,
    estimate_input_tokens,
    execution_blockers,
    preflight,
    prepared_payload,
)
from tendercite.services.llm.openai_compatible import OpenAICompatibleLLM


def configuration(**changes):
    # These numbers are invented unit-test constants, explicitly NOT live model pricing.
    return ComparisonConfig.model_validate(
        {
            "config_version": "cloud-llm-comparison-1",
            "base_url": "https://provider.invalid/v1",
            "model": "synthetic-test-model",
            "usable_context_tokens": 100_000,
            "context_budget_source": "Original synthetic test assumption",
            "max_output_tokens": 100,
            "token_safety_multiplier": "1.10",
            "pricing": {
                "scheme": "uncached_text_per_million_tokens",
                "base_url": "https://provider.invalid/v1",
                "model": "synthetic-test-model",
                "currency": "EUR",
                "input_per_million": "2",
                "output_per_million": "3",
                "source": "Artificial unit-test rates, not a provider quote",
                "as_of": "2026-01-01",
            },
            **changes,
        }
    )


def checked_preflight(config, corpus_data):
    return preflight(
        config,
        corpus_data,
        build_comparison_questions(corpus_data, top_k=config.rag_top_k),
        environment={},
    )


def test_byte_estimation_prices_full_payload_schema_and_maximum_output(comparison):  # noqa: F811
    config = configuration()
    question = build_comparison_questions(comparison)[0]
    payload = prepared_payload(config, question)
    tokens, wire = estimate_input_tokens(payload, config)
    assert "Return JSON matching this schema" in payload["messages"][1]["content"]
    assert payload["response_format"]["json_schema"]["schema"] == QAAnswer.model_json_schema()
    expected = int(
        (Decimal(len(wire) + 256) * Decimal("1.10")).to_integral_value(rounding=ROUND_CEILING)
    )
    assert tokens == expected
    report, prompts = checked_preflight(config, comparison)
    row = report["request_preflight"][0]
    assert Decimal(row["estimated_input_cost"]) == Decimal(tokens) * 2 / 1_000_000
    assert Decimal(row["maximum_output_cost"]) == Decimal("0.0003")
    assert Decimal(row["estimated_cost_with_safety_margin"]) == (
        Decimal(tokens) * 2 / 1_000_000 + Decimal("0.0003")
    ) * Decimal("1.25")
    assert report["status"] == "READY_DRY_RUN" and report["planned_requests"] == 4
    assert report["api_calls"] == 0 and not report["api_execution_available"]
    assert len(prompts) == 4


def test_both_arms_share_model_temperature_output_schema_and_instructions(comparison):  # noqa: F811
    config = configuration(temperature=0.4, request_timeout_seconds=12.0)
    report, prompts = checked_preflight(config, comparison)
    rag, full = (p["payload"] for p in prompts[:2])
    for field in ("model", "temperature", "max_tokens", "response_format"):
        assert rag[field] == full[field]
    assert rag["messages"][0] == full["messages"][0]
    assert rag["temperature"] == 0.4 and rag["max_tokens"] == 100
    assert report["configuration"]["request_timeout_seconds"] == 12.0


def test_context_failure_never_truncates_full_sources(comparison):  # noqa: F811
    config = configuration(usable_context_tokens=1)
    questions = build_comparison_questions(comparison)
    original = [q.model_dump() for q in questions]
    report, prompts = preflight(config, comparison, questions, environment={})
    assert report["status"] == "BLOCKED_PREFLIGHT"
    assert all("context_limit_exceeded" in r["failures"][0] for r in report["request_preflight"])
    assert [q.model_dump() for q in questions] == original
    assert all(p["prepared"] == q for p, q in zip(prompts, original, strict=True))


def test_per_request_cumulative_prior_spend_repetitions_and_safety_margin(comparison):  # noqa: F811
    config = configuration()
    base, _ = checked_preflight(config, comparison)
    total = Decimal(base["estimated_experiment_cost_with_margin"])
    maximum = max(
        Decimal(r["estimated_cost_with_safety_margin"]) for r in base["request_preflight"]
    )
    values = config.model_dump(mode="json")
    values["spending"].update(per_request=str(maximum / 2))
    report, _ = checked_preflight(ComparisonConfig.model_validate(values), comparison)
    assert any("per_request" in str(r["failures"]) for r in report["request_preflight"])
    values["spending"].update(
        per_request=str(maximum), cumulative=str(total), prior_estimated_spend="0"
    )
    assert (
        checked_preflight(ComparisonConfig.model_validate(values), comparison)[0]["status"]
        == "READY_DRY_RUN"
    )
    values["spending"]["prior_estimated_spend"] = "0.0001"
    assert (
        "cumulative_estimated_spending_limit_exceeded"
        in checked_preflight(ComparisonConfig.model_validate(values), comparison)[0]["failures"]
    )
    values["spending"]["prior_estimated_spend"] = "0"
    values["repetitions"] = 2
    doubled, _ = checked_preflight(ComparisonConfig.model_validate(values), comparison)
    assert Decimal(doubled["estimated_experiment_cost_with_margin"]) == total * 2
    assert doubled["planned_requests"] == 8 and doubled["status"] == "BLOCKED_PREFLIGHT"


@pytest.mark.parametrize(
    "mutation",
    [
        "missing",
        "wrong_model",
        "wrong_endpoint",
        "currency",
        "scheme",
        "negative",
        "nan",
        "missing_source",
        "tokenizer",
        "embedded_key",
    ],
)
def test_missing_or_unsupported_pricing_and_assumptions_are_rejected(mutation):
    value = configuration().model_dump(mode="json")
    if mutation == "missing":
        value.pop("pricing")
    elif mutation == "wrong_model":
        value["pricing"]["model"] = "other-model"
    elif mutation == "wrong_endpoint":
        value["pricing"]["base_url"] = "https://other.invalid/v1"
    elif mutation == "currency":
        value["pricing"]["currency"] = "USD"
    elif mutation == "scheme":
        value["pricing"]["scheme"] = "discounted_batch"
    elif mutation in {"negative", "nan"}:
        value["pricing"]["input_per_million"] = "-1" if mutation == "negative" else "NaN"
    elif mutation == "missing_source":
        value["pricing"].pop("source")
    elif mutation == "tokenizer":
        value["token_estimator"] = "unverified_guess"
    else:
        value["api_key"] = "synthetic-secret-must-not-be-in-config"
    with pytest.raises(ValueError):
        ComparisonConfig.model_validate(value)


def test_usd_requires_explicit_usd_limits_and_does_not_convert():
    value = configuration().model_dump(mode="json")
    value["pricing"]["currency"] = "USD"
    value["spending"].update(currency="USD", cumulative="15", per_request="0.50")
    config = ComparisonConfig.model_validate(value)
    assert config.spending.cumulative == Decimal("15")
    assert config.pricing.currency == "USD"


def test_eur_budget_defaults_cannot_be_silently_relabeled_as_usd():
    from evaluation.cloud_llm_preflight import SpendingLimits

    with pytest.raises(ValueError, match="explicit amounts"):
        SpendingLimits(currency="USD")


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://provider.invalid/v1",
        "https://u:secret@provider.invalid/v1",
        "https://provider.invalid/v1?key=secret",
        "https://provider.invalid/v1#fragment",
    ],
)
def test_cloud_endpoint_rejects_embedded_credentials_and_insecure_urls(endpoint):
    with pytest.raises(ValueError):
        configuration(base_url=endpoint)


def test_environment_overrides_are_validated_against_pricing_and_never_store_key():
    original = configuration().model_dump(mode="json")
    config = configured_from_environment(
        original,
        {
            "TENDERCITE_LLM_MODEL": "synthetic-test-model",
            "TENDERCITE_LLM_TEMPERATURE": "0.5",
            "TENDERCITE_LLM_MAX_OUTPUT_TOKENS": "250",
            "TENDERCITE_LLM_REQUEST_TIMEOUT_SECONDS": "10",
            "TENDERCITE_LLM_API_KEY": "test-only-private-value",
        },
    )
    assert config.temperature == 0.5 and config.max_output_tokens == 250
    assert "test-only-private-value" not in config.model_dump_json()
    with pytest.raises(ValueError, match="Pricing endpoint/model"):
        configured_from_environment(original, {"TENDERCITE_LLM_MODEL": "changed-model"})


def test_credentials_and_permission_are_execution_requirements_not_dry_run_requirements(comparison):  # noqa: F811
    config = configuration()
    report, prompts = checked_preflight(config, comparison)
    assert report["status"] == "READY_DRY_RUN" and not report["credential_environment_present"]
    assert "api_key_environment_variable_missing" in report["execution_blockers"]
    assert "provider_document_transmission_permission_required" in report["execution_blockers"]
    blockers = execution_blockers(
        config,
        ready=True,
        opt_in=True,
        transmission_confirmed=True,
        environment={config.api_key_env: "test-only-private-value"},
    )
    assert "execution_requires_official_openai_endpoint_and_gpt_6_1_sol" in blockers
    assert "execution_requires_20_USD_experiment_budget" in blockers
    assert "explicit_question_selection_required" in blockers
    report, _ = preflight(
        config,
        comparison,
        build_comparison_questions(comparison),
        transmission_confirmed=True,
        environment={config.api_key_env: "test-only-private-value"},
    )
    assert report["transmission_permission_declaration"]["provider_endpoint"] == config.base_url
    assert "test-only-private-value" not in json.dumps(report)
    assert "test-only-private-value" not in json.dumps(prompts)


def test_payload_only_preflight_never_creates_an_http_client(comparison, monkeypatch):  # noqa: F811
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **k: pytest.fail("Dry run attempted HTTP"))
    report, _ = checked_preflight(configuration(), comparison)
    assert report["api_calls"] == 0


def test_reused_adapter_mock_wire_matches_preflight_and_has_configured_limits(comparison):  # noqa: F811
    config = configuration(temperature=0.5, request_timeout_seconds=17.0)
    question = build_comparison_questions(comparison)[0]
    expected = prepared_payload(config, question)

    def handler(request):
        assert json.loads(request.content) == expected
        assert request.extensions["timeout"]["read"] == 17
        assert request.headers["Authorization"] == "Bearer test-only-key"
        answer = {
            "question_id": question.question_id,
            "status": "UNABLE_TO_ANSWER",
            "answer": "",
            "evidence": [],
            "inability_reason": "Synthetic mock response.",
        }
        return httpx.Response(
            200,
            json={
                "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(answer)}}]
            },
        )

    adapter = OpenAICompatibleLLM(
        base_url=config.base_url,
        model=config.model,
        api_key="test-only-key",
        temperature=config.temperature,
        max_output_tokens=config.max_output_tokens,
        request_timeout=config.request_timeout_seconds,
        transport=httpx.MockTransport(handler),
    )
    output = asyncio.run(adapter.generate_structured(**question.prompts(), response_model=QAAnswer))
    assert output.status == "UNABLE_TO_ANSWER"


@pytest.mark.parametrize(
    "kwargs",
    [
        {"temperature": float("nan")},
        {"temperature": 3},
        {"max_output_tokens": 0},
        {"max_output_tokens": True},
        {"request_timeout": 0},
    ],
)
def test_adapter_rejects_invalid_generation_parameters(kwargs):
    with pytest.raises(ValueError):
        OpenAICompatibleLLM(base_url="https://provider.invalid/v1", model="mock", **kwargs)
