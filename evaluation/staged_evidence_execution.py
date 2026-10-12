"""Bounded staged calls using the ORIGINAL ledger, adapter, accounting and OS lock."""

import json
import time
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

from evaluation.cloud_llm_contexts import QAAnswer
from evaluation.cloud_llm_execution import redact, response_record
from evaluation.cloud_llm_ledger import ExperimentLedger, amount, experiment_lock, ledger_path
from evaluation.cloud_llm_preflight import execution_blockers, preflight
from evaluation.cloud_llm_usage import check_reservation_rates
from evaluation.external_benchmark import confined
from evaluation.external_qa_spans import read_local_json, write_new_json
from evaluation.staged_evidence_consolidation import (
    consolidate,
    prepared_synthesis,
    validate_extraction,
    validate_synthesis,
)
from evaluation.staged_evidence_plan import digest, stage_config, synthesis_preflight
from evaluation.staged_evidence_schemas import (
    EXTRACTION_PROMPT_VERSION,
    EXTRACTION_SCHEMA_VERSION,
    SYNTHESIS_PROMPT_VERSION,
    SYNTHESIS_SCHEMA_VERSION,
    AtomicExtraction,
)
from evaluation.ted_acquisition import timestamp
from tendercite.services.llm.openai_compatible import (
    CompletionResponse,
    OpenAICompatibleLLM,
    ResponseLimitError,
)


def recorded_step(ledger, entry, step, row, config, corpus, manifest):
    """Revalidate saved output, wire identity and costs; a settled error is still a failure."""
    attempt = {
        "attempt_id": ledger.attempt_id(entry),
        "entry": entry,
        "completed": False,
        "record": {
            "generation_status": "INTERRUPTED_EXECUTION"
            if entry["status"] == "RESERVED"
            else "MISSING_RESPONSE_ARTIFACT",
            "accounting_status": "UNCERTAIN",
        },
    }
    try:
        metadata = entry["staged_step"]
        saved_plan, saved_plan_hash = read_local_json(Path(metadata["plan_dir"]) / "plan.json")
        if saved_plan != manifest or saved_plan_hash != metadata["plan_file_sha256"]:
            raise ValueError("Original staged plan artifact changed or is unavailable")
        record, file_hash = read_local_json(Path(entry["output_dir"]) / f"{step.strategy}.json")
        if (
            (entry["question_id"], entry["strategy"]) != (step.question_id, step.strategy)
            or metadata["plan_id"] != manifest["plan_id"]
            or metadata["step_id"] != step.step_id
            or (metadata["stage"], metadata["batch_id"]) != (step.stage, step.batch_id)
            or amount(entry["reserved_cost"]) != amount(row["estimated_cost_with_safety_margin"])
            or record.get("reservation_key") != entry["key"]
            or record.get("attempt_id") != entry["attempt_id"]
            or record.get("question_id") != step.question_id
            or record.get("strategy") != step.strategy
            or record.get("source_provenance") != corpus.provenance
            or record.get("staged_step") != metadata
            or record.get("configuration") != config.model_dump(mode="json")
            or record.get("request_payload_sha256") != row["request_payload_sha256"]
            or metadata["request_payload_sha256"] != row["request_payload_sha256"]
            or metadata["input_source_sha256"] != digest([s.model_dump() for s in step.sources])
            or metadata["configuration_sha256"] != digest(config.model_dump(mode="json"))
            or (entry.get("response_file_sha256") and entry["response_file_sha256"] != file_hash)
        ):
            raise ValueError("Staged response differs from its ledger/plan/source/payload binding")
        attempt["response_file_sha256"] = file_hash
        if "raw_response" not in record:
            if entry["status"] not in ("RESERVED", "UNCERTAIN"):
                raise ValueError("Settled staged response has no original provider output")
            attempt["record"] = record
            return attempt
        diagnostic, accounting = response_record(
            CompletionResponse(
                record["raw_response"].encode(),
                record["http_status"],
                record.get("provider_request_id"),
                record.get("latency_seconds", 0),
            ),
            step,
            corpus,
            config,
            response_model=AtomicExtraction if step.stage == "extraction" else QAAnswer,
            validate_output=validate_extraction
            if step.stage == "extraction"
            else validate_synthesis,
        )
        attempt["record"] = {**record, **diagnostic}
        if entry["status"] in ("SETTLED", "OVER_ESTIMATE") and (
            accounting is None
            or amount(accounting["usage_derived_cost"]) != amount(entry["usage_derived_cost"])
        ):
            raise ValueError("Staged provider usage differs from its preserved ledger accounting")
        validation = diagnostic.get("citation_validation", {})
        evidence_valid = (
            validation.get("extraction_valid", False)
            if step.stage == "extraction"
            else (
                validation.get("question_id_matches") is True
                and (
                    validation.get("citation_status") == "VERIFIED_QUOTE"
                    or (
                        validation.get("response_classification") == "UNABLE_TO_ANSWER"
                        and not validation.get("citations")
                    )
                )
            )
        )
        attempt["completed"] = (
            entry["status"] == "SETTLED"
            and diagnostic["response_validity"] == "VALID_STRUCTURED_ANSWER"
            and evidence_valid
        )
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
        attempt["error"] = str(exc)
        attempt["record"]["generation_status"] = "INVALID_ARTIFACT"
    return attempt


def extraction_attempts(ledger, manifest, steps, config, settings, corpus):
    if ledger.data["binding"]["provenance"] != corpus.provenance:
        raise ValueError("Staged experiment belongs to different acquisition/retrieval provenance")
    effective = stage_config(config, settings, "extraction")
    attempts = {}
    for step, batch in zip(steps, manifest["batches"], strict=True):
        entries = [e for e in ledger.data["entries"] if e["strategy"] == step.strategy]
        if len(entries) > 1:
            raise ValueError("Duplicate staged extraction history requires inspection")
        if entries:
            attempts[step.batch_id] = recorded_step(
                ledger, entries[0], step, batch["preflight"], effective, corpus, manifest
            )
    return attempts


def select_extractions(ledger, manifest, steps, attempts, batch_ids, continue_remaining, maximum):
    if type(maximum) is not int or not 1 <= maximum <= 3:
        raise ValueError("Explicit invocation limit must be between 1 and 3 requests")
    ledger.assert_accounting_ready()
    if any(not attempt["completed"] for attempt in attempts.values()):
        raise ValueError("A failed/incomplete extraction blocks continuation; no automatic retry")
    if bool(batch_ids) == bool(continue_remaining):
        raise ValueError("Select exact --batch-id values OR explicit --continue-remaining")
    by_id = {s.batch_id: s for s in steps}
    if continue_remaining:
        chosen = [s for s in steps if s.batch_id not in attempts][:maximum]
    else:
        if len(batch_ids) != len(set(batch_ids)) or len(batch_ids) > maximum:
            raise ValueError("Duplicate batch selection or request count exceeds invocation limit")
        if any(bid not in by_id for bid in batch_ids):
            raise ValueError("Selected batch ID does not belong to this immutable plan")
        if any(bid in attempts for bid in batch_ids):
            raise ValueError("Selected batch already attempted; paid duplicates are prohibited")
        chosen = [s for s in steps if s.batch_id in batch_ids]
    if not chosen:
        raise ValueError("No unexecuted extraction batches remain")
    remaining = sum(
        (
            amount(b["preflight"]["estimated_cost_with_safety_margin"])
            for b in manifest["batches"]
            if b["batch_id"] not in attempts
        ),
        Decimal("0"),
    )
    synthesis_already_attempted = any(
        e.get("staged_step", {}).get("plan_id") == manifest["plan_id"]
        and e.get("staged_step", {}).get("stage") == "synthesis"
        for e in ledger.data["entries"]
    )
    if not synthesis_already_attempted:
        remaining += amount(manifest["optional_synthesis_cost_ceiling_with_margin"])
    if ledger.committed() + remaining > ledger.limit:
        raise ValueError(
            "Remaining full-plan conservative ceiling exceeds shared experiment budget"
        )
    return chosen


def validate_gates(config, manifest, report, question_id, confirmed, environment):
    blockers = execution_blockers(
        config,
        ready=manifest["status"] == report["status"] == "READY_DRY_RUN",
        opt_in=True,
        transmission_confirmed=confirmed,
        question_selected=question_id == manifest["question_id"],
        environment=environment,
    )
    key = environment.get(config.api_key_env, "")
    if key and (not key.isascii() or any(c.isspace() or ord(c) < 33 for c in key)):
        blockers.append("invalid_api_key_environment_value")
    if blockers:
        raise ValueError("Staged execution blocked: " + "; ".join(blockers))
    check_reservation_rates(config)


async def transmit_steps(
    ledger,
    manifest,
    plan_dir,
    plan_file_hash,
    steps,
    config,
    corpus,
    report,
    requests,
    output_dir,
    environment,
    *,
    transport=None,
):
    """Caller holds the original OS lock for the entire invocation. No retries."""
    output_dir = confined(output_dir)
    key = environment["TENDERCITE_LLM_API_KEY"]
    adapter = OpenAICompatibleLLM(
        base_url=config.base_url,
        model=config.model,
        api_key=key,
        transport=transport,
        temperature=config.temperature,
        max_output_tokens=config.max_output_tokens,
        request_timeout=config.request_timeout_seconds,
        reasoning_effort=config.reasoning_effort,
    )
    output_dir.mkdir(parents=True, exist_ok=False)
    write_new_json(output_dir / "preflight.json", report)
    write_new_json(output_dir / "prompts.json", {"requests": requests})
    run = {
        "schema_version": "staged-evidence-execution-1",
        "plan_id": manifest["plan_id"],
        "question_id": manifest["question_id"],
        "status": "STOPPED",
        "api_calls_attempted": 0,
        "results": [],
        "started_at": timestamp(),
        "ledger": str(ledger.path),
        "full_planned_requests": manifest["planned_requests_including_optional_synthesis"],
        "full_plan_cost_ceiling_with_margin": manifest["full_plan_cost_ceiling_with_margin"],
    }
    invocation_started = time.perf_counter()
    print(
        json.dumps(
            {
                "status": "AUTHORIZED_SELECTION_PREFLIGHT",
                "api_calls_attempted": 0,
                "full_planned_requests": run["full_planned_requests"],
                "full_plan_cost_ceiling_with_margin": run["full_plan_cost_ceiling_with_margin"],
                "selected_requests": len(steps),
                "selected_estimated_cost_with_margin": report[
                    "estimated_experiment_cost_with_margin"
                ],
                "remaining_budget_usd": str(ledger.limit - ledger.committed()),
                "selected_steps": [
                    {
                        "stage": step.stage,
                        "batch_id": step.batch_id,
                        "input_token_estimate": row["input_token_estimate"],
                        "max_output_tokens": row["max_output_tokens"],
                        "estimated_cost_with_safety_margin": row[
                            "estimated_cost_with_safety_margin"
                        ],
                    }
                    for step, row in zip(steps, report["request_preflight"], strict=True)
                ],
            },
            indent=2,
        )
    )
    for step, row, request in zip(steps, report["request_preflight"], requests, strict=True):
        metadata = {
            "version": "staged-ledger-step-1",
            "plan_id": manifest["plan_id"],
            "step_id": step.step_id,
            "stage": step.stage,
            "batch_id": step.batch_id,
            "plan_dir": str(plan_dir),
            "plan_file_sha256": plan_file_hash,
            "request_payload_sha256": row["request_payload_sha256"],
            "input_source_sha256": digest([s.model_dump() for s in step.sources]),
            "configuration_sha256": digest(config.model_dump(mode="json")),
        }
        entry = ledger.reserve(
            step,
            row["estimated_cost_with_safety_margin"],
            output_dir,
            attempt_id=str(uuid4()),
            staged_step=metadata,
        )
        run["api_calls_attempted"] += 1
        accounting = None
        started = time.perf_counter()
        try:
            response = await adapter.request_completion(request["payload"])
            record, accounting = response_record(
                response,
                step,
                corpus,
                config,
                response_model=AtomicExtraction if step.stage == "extraction" else QAAnswer,
                validate_output=validate_extraction
                if step.stage == "extraction"
                else validate_synthesis,
            )
        except Exception as exc:
            record = {
                "generation_status": "TRANSPORT_ERROR",
                "response_validity": "INVALID_OR_INCOMPLETE",
                "accounting_status": "UNCERTAIN",
                "error": str(exc),
                "error_type": type(exc).__name__,
                "latency_seconds": time.perf_counter() - started,
                "configured_output_limit": config.max_output_tokens,
            }
            if isinstance(exc, ResponseLimitError):
                record.update(
                    raw_response_truncated=True,
                    raw_response=exc.response.body.decode("utf-8", errors="replace"),
                    http_status=exc.response.http_status,
                    provider_request_id=exc.response.request_id,
                )
        record.update(
            recorded_at=timestamp(),
            question_id=step.question_id,
            strategy=step.strategy,
            stage=step.stage,
            staged_step=metadata,
            attempt_id=entry["attempt_id"],
            reservation_key=entry["key"],
            configuration=config.model_dump(mode="json"),
            source_provenance=corpus.provenance,
            request_payload_sha256=row["request_payload_sha256"],
            preflight_estimate=row,
            prompt_version=EXTRACTION_PROMPT_VERSION
            if step.stage == "extraction"
            else SYNTHESIS_PROMPT_VERSION,
            response_schema_version=EXTRACTION_SCHEMA_VERSION
            if step.stage == "extraction"
            else SYNTHESIS_SCHEMA_VERSION,
        )
        write_new_json(output_dir / f"{step.strategy}.json", redact(record, key))
        ledger.reconcile(entry, accounting, record=redact(record, key))
        proof = recorded_step(ledger, entry, step, row, config, corpus, manifest)
        run["results"].append(
            {
                "step_id": step.step_id,
                "batch_id": step.batch_id,
                "stage": step.stage,
                "attempt_id": entry["attempt_id"],
                "reservation_key": entry["key"],
                "output_file": f"{step.strategy}.json",
                "completed": proof["completed"],
                "generation_status": record["generation_status"],
                "ledger_status": entry["status"],
                "usage_derived_cost": accounting["usage_derived_cost"] if accounting else None,
            }
        )
        if not proof["completed"]:
            run["error"] = (
                "Stopped on failed/incomplete generation, citation or accounting; no retry"
            )
            break
    else:
        run["status"] = "COMPLETED_SELECTED_STEPS"
    run.update(
        finished_at=timestamp(),
        invocation_latency_seconds=time.perf_counter() - invocation_started,
        committed_cost_or_reservations_usd=str(ledger.committed()),
        remaining_budget_usd=str(ledger.limit - ledger.committed()),
    )
    write_new_json(output_dir / "execution.json", run)
    return run


def checked_consolidation(ledger, manifest, steps, config, settings, corpus, directory):
    attempts = extraction_attempts(ledger, manifest, steps, config, settings, corpus)
    fresh = consolidate(manifest, attempts)
    saved, saved_hash = read_local_json(confined(directory) / "consolidation.json")
    if fresh != saved:
        raise ValueError("Saved consolidation is stale or differs from revalidated extraction")
    return fresh, saved_hash


async def execute_synthesis(
    manifest,
    config,
    settings,
    steps,
    corpus,
    plan_dir,
    plan_file_hash,
    consolidation_dir,
    output_dir,
    *,
    question_id,
    confirmed=False,
    environment,
    transport=None,
):
    if not ledger_path().exists():
        raise ValueError("Synthesis requires the original existing experiment ledger")
    with experiment_lock(ledger_path()):
        ledger = ExperimentLedger(ledger_path(), config, corpus)
        ledger.assert_accounting_ready()
        consolidated, _ = checked_consolidation(
            ledger, manifest, steps, config, settings, corpus, consolidation_dir
        )
        step = prepared_synthesis(manifest, consolidated, corpus)
        if any(
            e.get("staged_step", {}).get("plan_id") == manifest["plan_id"]
            and e.get("staged_step", {}).get("stage") == "synthesis"
            for e in ledger.data["entries"]
        ):
            raise ValueError("Final synthesis already attempted; no automatic retries")
        effective, report, requests = synthesis_preflight(config, settings, corpus, step)
        validate_gates(effective, manifest, report, question_id, confirmed, environment)
        if (
            ledger.committed() + amount(report["estimated_experiment_cost_with_margin"])
            > ledger.limit
        ):
            raise ValueError("Synthesis exceeds remaining shared experiment budget")
        return await transmit_steps(
            ledger,
            manifest,
            plan_dir,
            plan_file_hash,
            [step],
            effective,
            corpus,
            report,
            requests,
            output_dir,
            environment,
            transport=transport,
        )


async def execute_extractions(
    manifest,
    config,
    settings,
    steps,
    corpus,
    plan_dir,
    plan_file_hash,
    output_dir,
    *,
    question_id,
    batch_ids,
    continue_remaining=False,
    maximum=1,
    confirmed=False,
    environment,
    transport=None,
):
    if not ledger_path().exists():
        raise ValueError("Staged execution requires the original existing experiment ledger")
    effective = stage_config(config, settings, "extraction")
    with experiment_lock(ledger_path()):
        ledger = ExperimentLedger(ledger_path(), config, corpus)
        attempts = extraction_attempts(ledger, manifest, steps, config, settings, corpus)
        chosen = select_extractions(
            ledger, manifest, steps, attempts, batch_ids, continue_remaining, maximum
        )
        report, requests = preflight(
            effective, corpus, chosen, environment={}, response_model=AtomicExtraction
        )
        report["full_planned_requests"] = manifest["planned_requests_including_optional_synthesis"]
        report["full_plan_cost_ceiling_with_margin"] = manifest[
            "full_plan_cost_ceiling_with_margin"
        ]
        validate_gates(effective, manifest, report, question_id, confirmed, environment)
        return await transmit_steps(
            ledger,
            manifest,
            plan_dir,
            plan_file_hash,
            chosen,
            effective,
            corpus,
            report,
            requests,
            output_dir,
            environment,
            transport=transport,
        )
