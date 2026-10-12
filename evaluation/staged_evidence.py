"""Evaluation-only staged workflow: inventory, plan, inspect, extract, consolidate, synthesize."""

import argparse
import asyncio
import hashlib
import json
import logging
import os
from pathlib import Path

from pydantic import ValidationError

from evaluation.cloud_llm_comparison import runtime_metadata
from evaluation.cloud_llm_contexts import load_comparison_corpus
from evaluation.cloud_llm_ledger import ExperimentLedger, amount, experiment_lock, ledger_path
from evaluation.cloud_llm_preflight import configured_from_environment, preflight
from evaluation.external_benchmark import confined
from evaluation.external_document_cases import DATASET_REVISION, FAMILY
from evaluation.external_qa_spans import read_local_json, write_new_json
from evaluation.staged_evidence_consolidation import consolidate, prepared_synthesis
from evaluation.staged_evidence_execution import (
    checked_consolidation,
    execute_extractions,
    execute_synthesis,
    extraction_attempts,
    select_extractions,
)
from evaluation.staged_evidence_plan import (
    load_plan,
    page_inventory,
    plan,
    save_plan,
    stage_config,
    synthesis_preflight,
    unpriced_partition,
)
from evaluation.staged_evidence_schemas import AtomicExtraction, StagedSettings


def bound_existing_ledger(config, corpus):
    if not ledger_path().exists():
        raise ValueError("The original experiment ledger is unavailable; never create a new budget")
    return ExperimentLedger(ledger_path(), config, corpus)


def inspection(manifest, config, settings, steps, corpus):
    record = {
        "plan_id": manifest["plan_id"],
        "status": manifest["status"],
        "api_calls": 0,
        "planned_extraction_requests": len(steps),
        "planned_optional_synthesis_requests": 1,
        "full_plan_cost_ceiling_with_margin": manifest["full_plan_cost_ceiling_with_margin"],
        "currency": manifest["currency"],
        "source_pages": len(manifest["pages"]),
        "ledger_status": "UNAVAILABLE",
        "remaining_budget_usd": None,
        "execution_blockers": ["original_experiment_ledger_required"],
        "batches": manifest["batches"],
    }
    if not ledger_path().exists():
        return record
    with experiment_lock(ledger_path()):
        ledger = bound_existing_ledger(config, corpus)
        attempts = extraction_attempts(ledger, manifest, steps, config, settings, corpus)
        record.update(
            ledger_status="PRESENT",
            completed_batch_ids=[bid for bid, attempt in attempts.items() if attempt["completed"]],
            attempted_batch_ids=list(attempts),
            committed_cost_or_reservations_usd=str(ledger.committed()),
            remaining_budget_usd=str(ledger.limit - ledger.committed()),
            execution_blockers=[],
        )
        try:
            ledger.assert_accounting_ready()
        except ValueError as exc:
            record["execution_blockers"].append(str(exc))
        if any(not a["completed"] for a in attempts.values()):
            record["execution_blockers"].append("failed_extraction_prevents_continuation")
        pending = sum(
            (
                amount(b["preflight"]["estimated_cost_with_safety_margin"])
                for b in manifest["batches"]
                if b["batch_id"] not in attempts
            ),
            amount("0"),
        )
        if not any(
            e.get("staged_step", {}).get("plan_id") == manifest["plan_id"]
            and e.get("staged_step", {}).get("stage") == "synthesis"
            for e in ledger.data["entries"]
        ):
            pending += amount(manifest["optional_synthesis_cost_ceiling_with_margin"])
        record["remaining_plan_cost_ceiling_with_margin"] = str(pending)
        if pending + ledger.committed() > ledger.limit:
            record["execution_blockers"].append("remaining_full_plan_exceeds_persistent_budget")
    return record


def export_inventory(corpus, output_dir):
    output_dir = confined(output_dir)
    output_dir.mkdir(parents=True, exist_ok=False)
    rows = page_inventory(corpus)
    for row in rows:
        relative = f"pages/{row['document_id']}/page-{row['page_number']:04d}.txt"
        target = confined(output_dir / relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            corpus.pages[row["document_id"]][row["page_number"]], encoding="utf-8", newline=""
        )
        row["text_file"] = relative
    record = {
        "schema_version": "staged-source-inventory-1",
        "provenance": corpus.provenance,
        "documents": len(corpus.documents),
        "page_count": len(rows),
        "pages": rows,
        "api_calls": 0,
        "gold_used_for_source_selection": False,
    }
    write_new_json(output_dir / "source-inventory.json", record)
    return record


def execute_or_dry(args, corpus, loaded):
    manifest, config, settings, steps, _, plan_file_hash = loaded
    if args.question_id != manifest["question_id"]:
        raise ValueError("Exact --question-id must match the saved plan")
    if args.execute:
        if args.action == "extract":
            return asyncio.run(
                execute_extractions(
                    manifest,
                    config,
                    settings,
                    steps,
                    corpus,
                    confined(args.plan_dir),
                    plan_file_hash,
                    args.output_dir,
                    question_id=args.question_id,
                    batch_ids=args.batch_id,
                    continue_remaining=args.continue_remaining,
                    maximum=args.max_requests,
                    confirmed=args.confirm_transmission,
                    environment=os.environ,
                )
            )
        return asyncio.run(
            execute_synthesis(
                manifest,
                config,
                settings,
                steps,
                corpus,
                confined(args.plan_dir),
                plan_file_hash,
                args.consolidation_dir,
                args.output_dir,
                question_id=args.question_id,
                confirmed=args.confirm_transmission,
                environment=os.environ,
            )
        )
    if args.action == "synthesize":
        with experiment_lock(ledger_path()):
            ledger = bound_existing_ledger(config, corpus)
            ledger.assert_accounting_ready()
            consolidated, consolidated_hash = checked_consolidation(
                ledger, manifest, steps, config, settings, corpus, args.consolidation_dir
            )
            step = prepared_synthesis(manifest, consolidated, corpus)
            _, report, requests = synthesis_preflight(config, settings, corpus, step)
            report["consolidation_file_sha256"] = consolidated_hash
            report["remaining_budget_usd"] = str(ledger.limit - ledger.committed())
            if any(
                e.get("staged_step", {}).get("plan_id") == manifest["plan_id"]
                and e.get("staged_step", {}).get("stage") == "synthesis"
                for e in ledger.data["entries"]
            ):
                report["status"] = "BLOCKED_PREFLIGHT"
                report["failures"].append("synthesis_already_attempted")
            if (
                ledger.committed() + amount(report["estimated_experiment_cost_with_margin"])
                > ledger.limit
            ):
                report["status"] = "BLOCKED_PREFLIGHT"
                report["failures"].append("persistent_budget_exceeded")
    else:
        if ledger_path().exists():
            with experiment_lock(ledger_path()):
                ledger = bound_existing_ledger(config, corpus)
                attempts = extraction_attempts(ledger, manifest, steps, config, settings, corpus)
                selected = select_extractions(
                    ledger,
                    manifest,
                    steps,
                    attempts,
                    args.batch_id,
                    args.continue_remaining,
                    args.max_requests,
                )
        else:
            if args.continue_remaining:
                raise ValueError("Continuation requires the existing ledger")
            if (
                not args.batch_id
                or len(args.batch_id) != len(set(args.batch_id))
                or (len(args.batch_id) > args.max_requests)
            ):
                raise ValueError("Select bounded distinct batch IDs for offline request preflight")
            selected = [s for s in steps if s.batch_id in args.batch_id]
            if len(selected) != len(args.batch_id):
                raise ValueError("Unknown batch ID")
        effective = stage_config(config, settings, "extraction")
        report, requests = preflight(
            effective, corpus, selected, environment={}, response_model=AtomicExtraction
        )
    report["prompt_version"] = manifest["prompt_versions"][0 if args.action == "extract" else 1]
    report["response_schema_version"] = manifest["schema_versions"][
        0 if args.action == "extract" else 1
    ]
    report["full_planned_requests"] = manifest["planned_requests_including_optional_synthesis"]
    report["full_plan_cost_ceiling_with_margin"] = manifest["full_plan_cost_ceiling_with_margin"]
    args.output_dir.mkdir(parents=True, exist_ok=False)
    write_new_json(args.output_dir / "preflight.json", report)
    write_new_json(args.output_dir / "prompts.json", {"requests": requests})
    return report


def main(argv=None):
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument(
        "action",
        choices=(
            "inventory",
            "partition",
            "plan",
            "inspect",
            "extract",
            "consolidate",
            "synthesize",
        ),
    )
    cli.add_argument("--acquisition-dir", type=Path, required=True)
    cli.add_argument("--retrieval-results", type=Path, required=True)
    cli.add_argument("--output-dir", type=Path, required=True)
    cli.add_argument("--config", type=Path, help="Existing local comparison configuration/prices")
    cli.add_argument(
        "--stage-config", type=Path, help="Optional staged settings; sensible bounded defaults"
    )
    cli.add_argument("--plan-dir", type=Path)
    cli.add_argument("--consolidation-dir", type=Path)
    cli.add_argument("--question-id")
    cli.add_argument("--batch-id", action="append", default=[])
    cli.add_argument("--continue-remaining", action="store_true")
    cli.add_argument("--max-requests", type=int, choices=(1, 2, 3), default=1)
    modes = cli.add_mutually_exclusive_group()
    modes.add_argument("--dry-run", action="store_true", help="Default: never transmit")
    modes.add_argument("--execute", action="store_true")
    cli.add_argument("--confirm-transmission", action="store_true")
    cli.add_argument("--family", default=FAMILY)
    cli.add_argument("--dataset-revision", default=DATASET_REVISION)
    cli.add_argument("--expected-cases", type=int, default=9)
    cli.add_argument("--expected-documents", type=int, default=3)
    args = cli.parse_args(argv)
    try:
        args.output_dir = confined(args.output_dir)
        if args.output_dir.exists():
            raise ValueError(
                "Use a fresh ignored output directory; immutable outputs are preserved"
            )
        if args.execute and (
            args.action not in ("extract", "synthesize") or not args.confirm_transmission
        ):
            raise ValueError("Only extraction/synthesis may execute, with --confirm-transmission")
        if args.action in ("partition", "plan") and not args.question_id:
            raise ValueError("Partitioning/planning requires the exact --question-id")
        if args.action == "plan" and not args.config:
            raise ValueError("Priced planning requires existing --config with pricing")
        if args.action not in ("inventory", "partition", "plan") and not args.plan_dir:
            raise ValueError("This action requires --plan-dir")
        if args.action == "synthesize" and not args.consolidation_dir:
            raise ValueError("Synthesis requires a saved --consolidation-dir")
        corpus = load_comparison_corpus(
            args.acquisition_dir,
            args.retrieval_results,
            family=args.family,
            revision=args.dataset_revision,
            expected_cases=args.expected_cases,
            expected_documents=args.expected_documents,
        )
        if args.action == "inventory":
            report = export_inventory(corpus, args.output_dir)
        elif args.action == "partition":
            settings = (
                StagedSettings.model_validate(read_local_json(args.stage_config)[0])
                if args.stage_config
                else StagedSettings()
            )
            report, requests = unpriced_partition(corpus, args.question_id, settings)
            args.output_dir.mkdir(parents=True, exist_ok=False)
            write_new_json(args.output_dir / "partition-preview.json", report)
            for request in requests:
                write_new_json(
                    args.output_dir / "requests" / f"{request['prepared']['batch_id']}.json",
                    request,
                )
        elif args.action == "plan":
            raw, _ = read_local_json(args.config)
            config = configured_from_environment(raw)
            settings = (
                StagedSettings.model_validate(read_local_json(args.stage_config)[0])
                if args.stage_config
                else StagedSettings()
            )
            manifest, steps, requests = plan(corpus, args.question_id, config, settings)
            save_plan(args.output_dir, manifest, requests)
            report = inspection(manifest, config, settings, steps, corpus)
            report["runtime"] = runtime_metadata()
            report["staged_source_sha256"] = {
                p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                for p in Path(__file__).parent.glob("staged_evidence*.py")
            }
            write_new_json(args.output_dir / "inspection.json", report)
        else:
            loaded = load_plan(args.plan_dir, corpus)
            manifest, config, settings, steps, _, _ = loaded
            if args.action == "inspect":
                report = inspection(manifest, config, settings, steps, corpus)
                args.output_dir.mkdir(parents=True, exist_ok=False)
                write_new_json(args.output_dir / "inspection.json", report)
            elif args.action == "consolidate":
                with experiment_lock(ledger_path()):
                    ledger = bound_existing_ledger(config, corpus)
                    attempts = extraction_attempts(
                        ledger, manifest, steps, config, settings, corpus
                    )
                    report = consolidate(manifest, attempts)
                    _, ledger_hash = read_local_json(ledger_path())
                args.output_dir.mkdir(parents=True, exist_ok=False)
                write_new_json(args.output_dir / "consolidation.json", report)
                write_new_json(
                    args.output_dir / "consolidation-audit.json",
                    {
                        "plan_file_sha256": loaded[-1],
                        "ledger_sha256": ledger_hash,
                        "runtime": runtime_metadata(),
                    },
                )
            else:
                report = execute_or_dry(args, corpus, loaded)
        print(
            json.dumps(
                {
                    k: v
                    for k, v in report.items()
                    if k
                    in (
                        "status",
                        "api_calls",
                        "api_calls_attempted",
                        "plan_id",
                        "documents",
                        "page_count",
                        "planned_extraction_requests",
                        "planned_optional_synthesis_requests",
                        "currency",
                        "full_plan_cost_ceiling_with_margin",
                        "ledger_status",
                        "remaining_budget_usd",
                        "completed_batches",
                        "planned_batches",
                        "failures",
                        "error",
                        "execution_blockers",
                    )
                },
                indent=2,
            )
        )
        return 1 if report.get("status") in ("BLOCKED_PREFLIGHT", "STOPPED") else 0
    except (OSError, ValueError, KeyError, TypeError) as exc:
        if isinstance(exc, ValidationError):
            detail = "; ".join(e["msg"] for e in exc.errors(include_input=False, include_url=False))
        else:
            detail = str(exc)
        logging.error("Staged workflow rejected: %s", detail)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
