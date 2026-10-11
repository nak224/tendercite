"""Opt-in, exactly one question pair, no automatic retries or uncertain-cost continuation."""

import hashlib
import json
import re
import time
from decimal import Decimal

import httpx
from pydantic import ValidationError

from evaluation.cloud_llm_contexts import QAAnswer, validate_answer
from evaluation.cloud_llm_ledger import ExperimentLedger, experiment_lock, ledger_path
from evaluation.cloud_llm_preflight import execution_blockers
from evaluation.cloud_llm_usage import account_usage, check_reservation_rates
from evaluation.external_qa_spans import write_new_json
from evaluation.ted_acquisition import timestamp
from tendercite.services.llm.openai_compatible import OpenAICompatibleLLM, ResponseLimitError


def validate_execution(config, report, questions, *, confirmed, environment):
    selected = (
        len(questions) == 2
        and questions[0].question_id == questions[1].question_id
        and [q.strategy for q in questions] == ["e5_rag", "full_context"]
    )
    blockers = execution_blockers(
        config,
        ready=report["status"] == "READY_DRY_RUN",
        opt_in=True,
        transmission_confirmed=confirmed,
        question_selected=selected,
        environment=environment,
    )
    key = environment.get("TENDERCITE_LLM_API_KEY", "")
    if key and (not key.isascii() or any(c.isspace() or ord(c) < 33 for c in key)):
        blockers.append("invalid_api_key_environment_value")
    if blockers:
        raise ValueError("Execution blocked: " + "; ".join(blockers))
    check_reservation_rates(config)


def redact(value, key):
    # Never persist even a provider/transport error that echoes the credential.
    if isinstance(value, str):
        return value.replace(key, "[REDACTED]") if key else value
    if isinstance(value, list):
        return [redact(item, key) for item in value]
    if isinstance(value, dict):
        return {redact(k, key): redact(v, key) for k, v in value.items()}
    return value


def response_record(response, question, corpus, config):
    record = {
        "http_status": response.http_status,
        "provider_request_id": response.request_id,
        "latency_seconds": response.latency_seconds,
        "raw_response": response.body.decode("utf-8", errors="replace"),
        "raw_response_sha256": hashlib.sha256(response.body).hexdigest(),
        "response_validity": "INVALID_OR_INCOMPLETE",
        "accounting_status": "UNCERTAIN",
    }
    accounting = None
    try:
        body = json.loads(response.body)
        if not isinstance(body, dict):
            raise ValueError("Provider response is not a JSON object")
        record["actual_model"] = body.get("model")
        record["completion_id"] = body.get("id")
        if "error" in body:
            record["original_provider_error"] = body["error"]
        if not 200 <= response.http_status < 300:
            provider_error = body.get("error")
            provider_error = provider_error if isinstance(provider_error, dict) else {}
            record["primary_cause"] = (
                provider_error.get("code") or provider_error.get("type") or "http_error"
            )
            record["provider_error_code"] = provider_error.get("code")
            record["provider_error_type"] = provider_error.get("type")
            record["provider_error_message"] = provider_error.get("message")
            if body.get("usage") is not None:
                try:
                    accounting = account_usage(body["usage"], config)
                    record["accounting"] = accounting
                    record["accounting_status"] = "USAGE_RECORDED_NOT_INVOICE"
                except ValueError as exc:
                    record["usage_error"] = str(exc)
            record["error"] = f"HTTP {response.http_status}: {record['primary_cause']}"
            return record, accounting
        model = body.get("model")
        if model != config.model and not (
            isinstance(model, str)
            and re.fullmatch(re.escape(config.model) + r"-\d{4}-\d{2}-\d{2}", model)
        ):
            raise ValueError("Actual model is missing or differs from configured model")
        accounting = account_usage(body.get("usage"), config)
        record["accounting"] = accounting
        record["accounting_status"] = "USAGE_RECORDED_NOT_INVOICE"
        if accounting["completion_tokens"] > config.max_output_tokens:
            raise ValueError("Provider completion usage exceeds configured output-token limit")
        if response.http_status != 200:
            raise ValueError("HTTP error; original provider response preserved locally")
        choices = body.get("choices")
        if not isinstance(choices, list) or len(choices) != 1:
            raise ValueError("Expected one provider completion")
        choice = choices[0]
        record["completion_status"] = choice.get("finish_reason")
        message = choice.get("message", {})
        record["provider_native_metadata"] = {
            key: message[key] for key in ("annotations", "citations") if key in message
        }
        if choice.get("finish_reason") != "stop" or message.get("refusal"):
            raise ValueError("Completion was truncated, refused or incomplete")
        answer = QAAnswer.model_validate_json(message.get("content"))
        record["structured_answer"] = answer.model_dump()
        record["citation_validation"] = validate_answer(answer, question, corpus)
        record["response_validity"] = "VALID_STRUCTURED_ANSWER"
    except (ValueError, TypeError, KeyError, AttributeError, ValidationError) as exc:
        # Schema errors can contain source content; they are kept only in ignored local outputs.
        record["error"] = str(exc)
    return record, accounting


async def execute_pair(
    config,
    corpus,
    questions,
    report,
    prompts,
    output_dir,
    *,
    confirmed,
    environment,
    transport: httpx.AsyncBaseTransport | None = None,
):
    validate_execution(config, report, questions, confirmed=confirmed, environment=environment)
    if any(
        (output_dir / name).exists()
        for name in ("e5_rag.json", "full_context.json", "execution.json")
    ):
        raise ValueError("Existing response outputs are preserved; use a fresh directory")
    key = environment["TENDERCITE_LLM_API_KEY"]
    path = ledger_path()
    run = {
        "status": "STOPPED",
        "api_calls_attempted": 0,
        "question_id": questions[0].question_id,
        "ledger": str(path),
        "output_dir": str(output_dir),
        "results": [],
        "currency": "USD",
        "preflight_estimated_pair_cost_with_margin": report[
            "estimated_experiment_cost_with_margin"
        ],
        "final_provider_billing": "REQUIRES_DASHBOARD_RECONCILIATION",
    }
    adapter = OpenAICompatibleLLM(
        base_url=config.base_url,
        model=config.model,
        api_key=key,
        transport=transport,
        max_output_tokens=config.max_output_tokens,
        temperature=config.temperature,
        request_timeout=config.request_timeout_seconds,
        reasoning_effort=config.reasoning_effort,
    )
    with experiment_lock(path):
        ledger = ExperimentLedger(path, config, corpus)
        ledger.assert_pair_new(questions[0].question_id)
        pair_estimate = Decimal(report["estimated_experiment_cost_with_margin"])
        if ledger.committed() + pair_estimate > Decimal("20"):
            raise ValueError("Selected pair cannot fit the remaining persistent 20 USD budget")
        for question, row, prompt in zip(
            questions, report["request_preflight"], prompts, strict=True
        ):
            try:
                entry = ledger.reserve(
                    question, row["estimated_cost_with_safety_margin"], output_dir
                )
            except ValueError as exc:
                run["error"] = str(exc)
                break
            started = time.perf_counter()
            run["api_calls_attempted"] += 1
            accounting = None
            try:
                # Use the exact preflight-built payload. No retries, fallback, or schema weakening.
                response = await adapter.request_completion(prompt["payload"])
                record, accounting = response_record(response, question, corpus, config)
            except Exception as exc:
                # Any failure after an attempt may have ambiguous billing. Never retry it.
                record = {
                    "response_validity": "INVALID_OR_INCOMPLETE",
                    "accounting_status": "UNCERTAIN",
                    "error": str(exc),
                    "error_type": type(exc).__name__,
                    "latency_seconds": time.perf_counter() - started,
                }
                if isinstance(exc, ResponseLimitError):
                    record["raw_response_truncated"] = True
                    record["raw_response"] = exc.response.body.decode("utf-8", errors="replace")
                    record["http_status"] = exc.response.http_status
                    record["provider_request_id"] = exc.response.request_id
            record.update(
                {
                    "recorded_at": timestamp(),
                    "question_id": question.question_id,
                    "strategy": question.strategy,
                    "request_payload_sha256": row["request_payload_sha256"],
                    "preflight_estimate": row,
                    "configuration": config.model_dump(mode="json"),
                    "source_provenance": corpus.provenance,
                    "reservation_key": entry["key"],
                }
            )
            # Preserve output before releasing any reservation. A filesystem/crash failure leaves
            # RESERVED durable and blocks another invocation until manual billing reconciliation.
            write_new_json(output_dir / f"{question.strategy}.json", redact(record, key))
            ledger.reconcile(entry, accounting)
            run["results"].append(
                {
                    "strategy": question.strategy,
                    "output_file": f"{question.strategy}.json",
                    "accounting_status": record["accounting_status"],
                    "ledger_status": entry["status"],
                    "response_validity": record["response_validity"],
                    "usage_derived_cost": None
                    if accounting is None
                    else accounting["usage_derived_cost"],
                }
            )
            if (
                entry["status"] != "SETTLED"
                or record["response_validity"] != "VALID_STRUCTURED_ANSWER"
            ):
                run["error"] = (
                    "Further requests stopped; inspect original local response/accounting"
                )
                break
        else:
            run["status"] = "COMPLETED_PAIR"
        run["persistent_committed_cost_or_reservations"] = str(ledger.committed())
        run["remaining_budget"] = str(Decimal("20") - ledger.committed())
        run["known_usage_derived_subtotal"] = str(
            sum(
                (
                    Decimal(r["usage_derived_cost"])
                    for r in run["results"]
                    if r["usage_derived_cost"] is not None
                ),
                Decimal("0"),
            )
        )
        run["accounting_complete"] = len(run["results"]) == run["api_calls_attempted"] and all(
            r["usage_derived_cost"] is not None for r in run["results"]
        )
        run["usage_derived_run_cost"] = (
            run["known_usage_derived_subtotal"] if run["accounting_complete"] else None
        )
        write_new_json(output_dir / "execution.json", run)
    return run
