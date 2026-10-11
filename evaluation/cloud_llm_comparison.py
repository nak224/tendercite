"""Cloud QA comparison foundation: local preflight only; paid execution is disabled."""

import argparse
import hashlib
import json
import logging
import platform
from pathlib import Path

from pydantic import ValidationError

from evaluation.cloud_llm_contexts import build_comparison_questions, load_comparison_corpus
from evaluation.cloud_llm_preflight import configured_from_environment, preflight
from evaluation.external_benchmark import confined
from evaluation.external_qa_spans import read_local_json, write_new_json
from evaluation.ted_acquisition import timestamp


def run_dry(
    config_path, acquisition_dir, retrieval_results, output_dir, *, transmission_confirmed=False
):
    output_dir = confined(output_dir)
    if output_dir.exists():
        raise ValueError("Use a fresh ignored output directory; existing files are preserved")
    record = {"status": "BLOCKED_INPUT", "api_calls": 0, "api_execution_available": False}
    try:
        raw, config_hash = read_local_json(config_path)
        if not isinstance(raw, dict):
            raise ValueError("Configuration must be a JSON object")
        config = configured_from_environment(raw)
        corpus = load_comparison_corpus(acquisition_dir, retrieval_results)
        questions = build_comparison_questions(corpus, top_k=config.rag_top_k)
        record, prompts = preflight(
            config, corpus, questions, transmission_confirmed=transmission_confirmed
        )
        record["config_file_sha256"] = config_hash
        write_new_json(output_dir / "prompts.json", {"requests": prompts})
    except ValidationError as exc:
        record["status"] = "BLOCKED_INPUT"
        record["failure"] = "; ".join(
            f"{'.'.join(map(str, e['loc']))}: {e['msg']}"
            for e in exc.errors(include_input=False, include_url=False)
        )
    except (OSError, ValueError, KeyError, TypeError) as exc:
        record["status"] = "BLOCKED_INPUT"
        record["failure"] = str(exc)
    record.update(
        {
            "recorded_at": timestamp(),
            "python": platform.python_version(),
            "source_sha256": {
                name: hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest()
                for name in (
                    "cloud_llm_contexts.py",
                    "cloud_llm_preflight.py",
                    "cloud_llm_comparison.py",
                )
            },
        }
    )
    write_new_json(output_dir / "preflight.json", record)
    return record


def main(argv=None):
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--config", type=Path, required=True, help="Local ignored configuration JSON")
    cli.add_argument("--acquisition-dir", type=Path, required=True)
    cli.add_argument("--retrieval-results", type=Path, required=True)
    cli.add_argument("--output-dir", type=Path, required=True, help="Fresh ignored directory")
    mode = cli.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="Default; never makes API calls")
    mode.add_argument(
        "--execute", action="store_true", help="Reserved; always rejected in this milestone"
    )
    cli.add_argument(
        "--confirm-transmission",
        action="store_true",
        help="Declare permission for these artifacts/provider; does not enable execution",
    )
    args = cli.parse_args(argv)
    if args.execute:
        logging.error(
            "Paid API execution is disabled in this milestone, including with explicit opt-in"
        )
        return 1
    try:
        report = run_dry(
            args.config,
            args.acquisition_dir,
            args.retrieval_results,
            args.output_dir,
            transmission_confirmed=args.confirm_transmission,
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
                )
                if k in report
            },
            indent=2,
        )
    )
    return 0 if report["status"] == "READY_DRY_RUN" else 1


if __name__ == "__main__":
    raise SystemExit(main())
