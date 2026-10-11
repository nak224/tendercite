"""Offline, explicitly priced token/cost preflight; never a provider billing cap."""

import hashlib
import json
import os
from datetime import date
from decimal import ROUND_CEILING, Decimal
from typing import Annotated, Literal
from urllib.parse import urlsplit

from pydantic import Field, StrictInt, field_validator, model_validator

from evaluation.cloud_llm_contexts import (
    PROMPT_VERSION,
    SCHEMA_VERSION,
    QAAnswer,
    StrictModel,
)
from tendercite.core.config import Settings
from tendercite.services.llm.openai_compatible import OpenAICompatibleLLM

Money = Annotated[Decimal, Field(ge=0, allow_inf_nan=False)]
PositiveMoney = Annotated[Decimal, Field(gt=0, allow_inf_nan=False)]
Nonblank = Annotated[str, Field(strict=True, min_length=1, pattern=r"\S")]
Currency = Literal["EUR", "USD"]


class Pricing(StrictModel):
    scheme: Literal["uncached_text_per_million_tokens"]
    base_url: Nonblank
    model: Nonblank
    currency: Currency
    input_per_million: Money
    output_per_million: Money
    source: Nonblank
    as_of: date


class UsageTier(StrictModel):
    above_prompt_tokens: Annotated[StrictInt, Field(ge=0)]
    input_per_million: Money
    output_per_million: Money
    cached_input_per_million: Money | None = None
    cache_write_input_per_million: Money | None = None


class UsagePricing(StrictModel):
    """Optional documented accounting rates; never used to retune dry-run estimates."""

    source: Nonblank
    as_of: date
    cached_input_per_million: Money | None = None
    cache_write_input_per_million: Money | None = None
    tiers: list[UsageTier] = Field(default_factory=list)

    @model_validator(mode="after")
    def ordered_tiers(self):
        thresholds = [tier.above_prompt_tokens for tier in self.tiers]
        if thresholds != sorted(set(thresholds)):
            raise ValueError("Usage tiers must have unique ascending prompt-token thresholds")
        return self


class SpendingLimits(StrictModel):
    currency: Currency = "EUR"
    per_request: PositiveMoney = Decimal("1")
    cumulative: PositiveMoney = Decimal("20")
    prior_estimated_spend: Money = Decimal("0")
    safety_multiplier: Annotated[Decimal, Field(gt=1, le=10, allow_inf_nan=False)] = Decimal("1.25")

    @model_validator(mode="after")
    def explicit_non_eur_limits(self):
        if self.currency != "EUR" and not {"per_request", "cumulative"}.issubset(
            self.model_fields_set
        ):
            raise ValueError(
                "USD spending limits require explicit amounts; EUR defaults cannot be relabeled"
            )
        return self


class ComparisonConfig(StrictModel):
    config_version: Literal["cloud-llm-comparison-1"]
    base_url: Nonblank
    model: Nonblank
    api_key_env: Annotated[str, Field(strict=True, pattern=r"^[A-Z][A-Z0-9_]*$")] = (
        "TENDERCITE_LLM_API_KEY"
    )
    temperature: Annotated[float, Field(strict=True, ge=0, le=2, allow_inf_nan=False)] = 0.0
    max_output_tokens: Annotated[StrictInt, Field(ge=1)] = 2048
    request_timeout_seconds: Annotated[float, Field(strict=True, gt=0, allow_inf_nan=False)] = 90.0
    reasoning_effort: Literal["low"] = "low"
    rag_top_k: Annotated[StrictInt, Field(ge=1, le=50)] = 12
    usable_context_tokens: Annotated[StrictInt, Field(ge=1)]
    context_budget_source: Nonblank
    token_estimator: Literal["utf8_bytes_v1"] = "utf8_bytes_v1"
    token_safety_multiplier: Annotated[Decimal, Field(ge=1, le=10, allow_inf_nan=False)] = Decimal(
        "1.10"
    )
    request_overhead_tokens: Annotated[StrictInt, Field(ge=0)] = 256
    repetitions: Annotated[StrictInt, Field(ge=1, le=10)] = 1
    pricing: Pricing
    usage_pricing: UsagePricing | None = None
    spending: SpendingLimits = Field(default_factory=SpendingLimits)

    @field_validator("base_url")
    @classmethod
    def endpoint(cls, value):
        Settings.validate_provider_url(value)
        if urlsplit(value).scheme != "https":
            raise ValueError("Cloud evaluation requires an HTTPS base URL")
        return value.rstrip("/")

    @model_validator(mode="after")
    def consistent_assumptions(self):
        if (self.pricing.base_url.rstrip("/"), self.pricing.model) != (self.base_url, self.model):
            raise ValueError("Pricing endpoint/model must match the selected endpoint/model")
        if self.pricing.currency != self.spending.currency:
            raise ValueError(
                "Pricing and budget currencies must match; no currency conversion is performed"
            )
        if self.spending.per_request > self.spending.cumulative:
            raise ValueError("Per-request spending limit exceeds cumulative limit")
        return self


def configured_from_environment(value: dict, environment=None) -> ComparisonConfig:
    environment = os.environ if environment is None else environment
    data = value.copy()
    for field, variable in (
        ("base_url", "TENDERCITE_LLM_BASE_URL"),
        ("model", "TENDERCITE_LLM_MODEL"),
        ("temperature", "TENDERCITE_LLM_TEMPERATURE"),
        ("max_output_tokens", "TENDERCITE_LLM_MAX_OUTPUT_TOKENS"),
        ("request_timeout_seconds", "TENDERCITE_LLM_REQUEST_TIMEOUT_SECONDS"),
    ):
        if raw := environment.get(variable):
            data[field] = raw if field in {"base_url", "model"} else json.loads(raw)
    return ComparisonConfig.model_validate(data)


def prepared_payload(config: ComparisonConfig, question) -> dict:
    # Payload-only reuse of the same adapter: neither keys nor HTTP clients are needed.
    adapter = OpenAICompatibleLLM(
        base_url=config.base_url,
        model=config.model,
        temperature=config.temperature,
        max_output_tokens=config.max_output_tokens,
        request_timeout=config.request_timeout_seconds,
        reasoning_effort=config.reasoning_effort,
    )
    return adapter.build_payload(**question.prompts(), response_model=QAAnswer)


def estimate_input_tokens(payload: dict, config: ComparisonConfig) -> tuple[int, bytes]:
    wire = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()
    # Both schema copies and all source/identifier/JSON text count. Conservative estimate,
    # not a universal tokenizer guarantee; an exact provider tokenizer is a later check.
    estimate = Decimal(len(wire) + config.request_overhead_tokens) * config.token_safety_multiplier
    return int(estimate.to_integral_value(rounding=ROUND_CEILING)), wire


def execution_blockers(
    config,
    *,
    ready,
    opt_in=False,
    transmission_confirmed=False,
    question_selected=False,
    environment=None,
):
    environment = os.environ if environment is None else environment
    blockers = []
    if (config.base_url, config.model) != ("https://api.openai.com/v1", "gpt-6.1-sol"):
        blockers.append("execution_requires_official_openai_endpoint_and_gpt_6_1_sol")
    if config.api_key_env != "TENDERCITE_LLM_API_KEY":
        blockers.append("execution_requires_TENDERCITE_LLM_API_KEY")
    if config.spending.currency != "USD" or config.spending.cumulative != Decimal("20"):
        blockers.append("execution_requires_20_USD_experiment_budget")
    if config.repetitions != 1:
        blockers.append("pilot_requires_one_pair_without_repetitions")
    if not question_selected:
        blockers.append("explicit_question_selection_required")
    if not opt_in:
        blockers.append("explicit_execution_opt_in_required")
    if not transmission_confirmed:
        blockers.append("provider_document_transmission_permission_required")
    if not environment.get(config.api_key_env, "").strip():
        blockers.append("api_key_environment_variable_missing")
    if not ready:
        blockers.append("context_or_cost_preflight_not_ready")
    return blockers


def preflight(config, corpus, questions, *, transmission_confirmed=False, environment=None):
    environment = os.environ if environment is None else environment
    rows, prompts, total = [], [], Decimal("0")
    for question in questions:
        payload = prepared_payload(config, question)
        tokens, wire = estimate_input_tokens(payload, config)
        input_cost = Decimal(tokens) * config.pricing.input_per_million / 1_000_000
        output_cost = (
            Decimal(config.max_output_tokens) * config.pricing.output_per_million / 1_000_000
        )
        safe_cost = (input_cost + output_cost) * config.spending.safety_multiplier
        failures = []
        if tokens + config.max_output_tokens > config.usable_context_tokens:
            failures.append("context_limit_exceeded; complete source text preserved")
        if safe_cost > config.spending.per_request:
            failures.append("per_request_estimated_spending_limit_exceeded")
        total += safe_cost * config.repetitions
        rows.append(
            {
                "question_id": question.question_id,
                "question_type": question.question_type,
                "strategy": question.strategy,
                "source_count": len(question.sources),
                "source_characters": sum(len(s.text) for s in question.sources),
                "input_token_estimate": tokens,
                "max_output_tokens": config.max_output_tokens,
                "usable_context_tokens": config.usable_context_tokens,
                "request_payload_sha256": hashlib.sha256(wire).hexdigest(),
                "effective_generation": {
                    key: payload[key]
                    for key in (
                        "model",
                        "temperature",
                        "max_tokens",
                        "max_completion_tokens",
                        "reasoning_effort",
                    )
                    if key in payload
                },
                "strict_structured_outputs": payload["response_format"]["json_schema"].get(
                    "strict", False
                ),
                "estimated_input_cost": str(input_cost),
                "maximum_output_cost": str(output_cost),
                "estimated_cost_with_safety_margin": str(safe_cost),
                "currency": config.pricing.currency,
                "repetitions": config.repetitions,
                "failures": failures,
                "feasible": not failures,
            }
        )
        prompts.append(
            {
                "question_id": question.question_id,
                "strategy": question.strategy,
                "prepared": question.model_dump(),
                "payload": payload,
            }
        )
    failures = []
    if not rows:
        failures.append("no_prepared_questions")
    if any(r["failures"] for r in rows):
        failures.append("one_or_more_requests_failed_preflight")
    cumulative = total + config.spending.prior_estimated_spend
    if cumulative > config.spending.cumulative:
        failures.append("cumulative_estimated_spending_limit_exceeded")
    ready = not failures
    report = {
        "status": "READY_DRY_RUN" if ready else "BLOCKED_PREFLIGHT",
        "api_calls": 0,
        "embeddings_called": False,
        "api_execution_available": False,
        "prompt_version": PROMPT_VERSION,
        "response_schema_version": SCHEMA_VERSION,
        "configuration": config.model_dump(mode="json"),
        "provenance": corpus.provenance,
        "planned_requests": len(rows) * config.repetitions,
        "request_preflight": rows,
        "failures": failures,
        "estimated_experiment_cost_with_margin": str(total),
        "cumulative_estimated_spend_with_margin": str(cumulative),
        "currency": config.pricing.currency,
        "credential_environment_variable": config.api_key_env,
        "credential_environment_present": bool(environment.get(config.api_key_env, "").strip()),
        "transmission_permission_declaration": {
            "confirmed": transmission_confirmed,
            "provider_endpoint": config.base_url,
            "model": config.model,
            "input_provenance": corpus.provenance,
        },
        "execution_blockers": execution_blockers(
            config,
            ready=ready,
            transmission_confirmed=transmission_confirmed,
            environment=environment,
        ),
        "token_assumption": (
            "UTF-8 request bytes plus configured overhead and margin; not exact tokenization"
        ),
        "pricing_assumption": (
            "Operator-declared uncached text rates; no live pricing verification or FX conversion"
        ),
        "billing_limit_warning": (
            "Local estimates are safeguards, not guaranteed provider billing caps"
        ),
    }
    return report, prompts
