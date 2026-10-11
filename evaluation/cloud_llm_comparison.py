"""Cloud QA comparison: local dry-run by default, explicit guarded question-pair execution."""

import argparse
import asyncio
import hashlib
import json
import logging
import os
import platform
from pathlib import Path

from pydantic import ValidationError

from evaluation.cloud_llm_contexts import build_comparison_questions, load_comparison_corpus
from evaluation.cloud_llm_execution import execute_pair, validate_execution
from evaluation.cloud_llm_preflight import configured_from_environment, preflight
from evaluation.external_benchmark import confined
from evaluation.external_qa_spans import read_local_json, write_new_json
from evaluation.ted_acquisition import timestamp


def run_dry(
    config_path,
    acquisition_dir,
    retrieval_results,
    output_dir,
    *,
    transmission_confirmed=False,
    question_id=None,
    execute=False,
    previous_preflight=None,
):
    output_dir = confined(output_dir)
    if output_dir.exists():
        raise ValueError("Use a fresh ignored output directory; existing files are preserved")
    record = {"status": "BLOCKED_INPUT", "api_calls": 0, "api_execution_available": False}
    execution_started = False
    try:
        raw, config_hash = read_local_json(config_path)
        if not isinstance(raw, dict):
            raise ValueError("Configuration must be a JSON object")
        config = configured_from_environment(raw)
        corpus = load_comparison_corpus(acquisition_dir, retrieval_results)
        questions = build_comparison_questions(corpus, top_k=config.rag_top_k)
        if execute and not question_id:
            raise ValueError("Execution requires an explicit --question-id")
        if question_id:
            questions = [q for q in questions if q.question_id == question_id]
            if len(questions) != 2:
                raise ValueError("Selected question ID is not in the original eligible cohort")
        record, prompts = preflight(
            config, corpus, questions, transmission_confirmed=transmission_confirmed
        )
        record["config_file_sha256"] = config_hash
        record["previous_dry_run"] = {"available": False}
        if previous_preflight:
            previous, previous_hash = read_local_json(previous_preflight)
            previous_config = configured_from_environment(previous["configuration"], environment={})
            fields = (
                "pricing",
                "token_estimator",
                "token_safety_multiplier",
                "request_overhead_tokens",
            )
            if previous.get("status") != "READY_DRY_RUN" or any(
                getattr(previous_config, f) != getattr(config, f) for f in fields
            ):
                raise ValueError(
                    "Previous preflight pricing/token assumptions changed or not ready"
                )
            record["previous_dry_run"] = {
                "available": True,
                "sha256": previous_hash,
                "estimated_cost_with_margin": previous["estimated_experiment_cost_with_margin"],
                "currency": previous["currency"],
                "planned_requests": previous["planned_requests"],
            }
        write_new_json(output_dir / "prompts.json", {"requests": prompts})
        if execute:
            validate_execution(
                config, record, questions, confirmed=transmission_confirmed, environment=os.environ
            )
            # Persist the reviewed estimates/provenance BEFORE any request can be attempted.
            record.update(runtime_metadata())
            write_new_json(output_dir / "preflight.json", record)
            execution_started = True
            return asyncio.run(
                execute_pair(
                    config,
                    corpus,
                    questions,
                    record,
                    prompts,
                    output_dir,
                    confirmed=transmission_confirmed,
                    environment=os.environ,
                )
            )
    except ValidationError as exc:
        record["status"] = "BLOCKED_INPUT"
        record["failure"] = "; ".join(
            f"{'.'.join(map(str, e['loc']))}: {e['msg']}"
            for e in exc.errors(include_input=False, include_url=False)
        )
    except (OSError, ValueError, KeyError, TypeError) as exc:
        record["status"] = "BLOCKED_INPUT"
        record["failure"] = str(exc)
    if execution_started:
        record["api_calls"] = None
        record["accounting_status"] = "CHECK_PERSISTENT_LEDGER_FOR_INTERRUPTED_EXECUTION"
    record.update(runtime_metadata())
    if (output_dir / "preflight.json").exists():
        write_new_json(output_dir / "blocked-execution.json", record)
    else:
        write_new_json(output_dir / "preflight.json", record)
    return record


def runtime_metadata():
    return {
        "recorded_at": timestamp(),
        "python": platform.python_version(),
        "adapter_sha256": hashlib.sha256(
            Path(__file__)
            .resolve()
            .parents[1]
            .joinpath("src/tendercite/services/llm/openai_compatible.py")
            .read_bytes()
        ).hexdigest(),
        "source_sha256": {
            name: hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest()
            for name in (
                "cloud_llm_contexts.py",
                "cloud_llm_preflight.py",
                "cloud_llm_comparison.py",
                "cloud_llm_execution.py",
                "cloud_llm_usage.py",
                "cloud_llm_ledger.py",
            )
        },
    }


def main(argv=None):
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--config", type=Path, required=True, help="Local ignored configuration JSON")
    cli.add_argument("--acquisition-dir", type=Path, required=True)
    cli.add_argument("--retrieval-results", type=Path, required=True)
    cli.add_argument("--output-dir", type=Path, required=True, help="Fresh ignored directory")
    mode = cli.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="Default; never makes API calls")
    mode.add_argument(
        "--execute", action="store_true", help="Explicit operator opt-in; one pair only"
    )
    cli.add_argument("--question-id", help="Original eligible ID; required for execution")
    cli.add_argument(
        "--previous-preflight", type=Path, help="Optional original READY_DRY_RUN report"
    )
    cli.add_argument(
        "--confirm-transmission",
        action="store_true",
        help="Confirm permission to transmit these artifacts to the selected provider",
    )
    args = cli.parse_args(argv)
    if args.execute and (not args.question_id or not args.confirm_transmission):
        logging.error("Execution requires --question-id and --confirm-transmission")
        return 1
    try:
        report = run_dry(
            args.config,
            args.acquisition_dir,
            args.retrieval_results,
            args.output_dir,
            transmission_confirmed=args.confirm_transmission,
            question_id=args.question_id,
            execute=args.execute,
            previous_preflight=args.previous_preflight,
        )
    except (OSError, ValueError) as exc:
        logging.error("Preflight not started: %s", exc)
        return 1
    print(
        json.dumps(
            {
                k: report[k]
                for k in (
                    "status",
                    "api_calls",
                    "api_execution_available",
                    "currency",
                    "planned_requests",
                    "estimated_experiment_cost_with_margin",
                    "failures",
                    "failure",
                    "api_calls_attempted",
                    "usage_derived_run_cost",
                    "remaining_budget",
                    "output_dir",
                    "results",
                    "error",
                )
                if k in report
            },
            indent=2,
        )
    )
    return 0 if report["status"] in ("READY_DRY_RUN", "COMPLETED_PAIR") else 1


if __name__ == "__main__":
    raise SystemExit(main())
