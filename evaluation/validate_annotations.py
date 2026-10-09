"""Offline annotation validation and explicit human-verified gold export."""

import argparse
import hashlib
import json
import logging
import tempfile
from pathlib import Path

from evaluation.annotations import SPLIT_SEED, validate_annotations

logger = logging.getLogger(__name__)


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, prefix=".annotation-", delete=False
    ) as temporary:
        temporary.write(json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True) + "\n")
        snapshot = Path(temporary.name)
    try:
        snapshot.replace(path)
    finally:
        snapshot.unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    cli = argparse.ArgumentParser(
        description="Validate human annotations against local corpus PDFs"
    )
    cli.add_argument("annotations", type=Path, help="Versioned human-authored annotation JSON")
    cli.add_argument("--corpus-dir", type=Path, default=Path("data/evaluation/real-pdf-1"))
    cli.add_argument("--report", type=Path, help="Default: CORPUS_DIR/annotation-validation.json")
    cli.add_argument(
        "--export", type=Path, help="Write gold JSON containing only verified valid labels"
    )
    cli.add_argument("--split-seed", type=int, default=SPLIT_SEED)
    args = cli.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    try:
        report = args.report or args.corpus_dir / "annotation-validation.json"
        outputs = [report] + ([args.export] if args.export else [])
        protected = {args.annotations.resolve(), (args.corpus_dir / "manifest.json").resolve()}
        if len({path.resolve() for path in outputs}) != len(outputs):
            raise ValueError("Report and export paths must be distinct")
        for path in outputs:
            if path.resolve() in protected or path.resolve().is_relative_to(
                (args.corpus_dir / "pdfs").resolve()
            ):
                raise ValueError("Outputs must not overwrite annotations, corpus manifest or PDFs")
        raw = args.annotations.read_bytes()
        outcome = validate_annotations(
            json.loads(raw),
            args.corpus_dir,
            seed=args.split_seed,
            source_sha256=hashlib.sha256(raw).hexdigest(),
        )
        write_json(report, outcome.report)
        if args.export:
            write_json(args.export, outcome.gold)
        for result in outcome.report["results"]:
            for reason in result["exclusion_reasons"]:
                logger.warning(
                    "Annotation %s (index %d): %s",
                    result["annotation_id"],
                    result["input_index"],
                    reason,
                )
        for error in outcome.report["corpus_errors"]:
            logger.error("%s", error)
        summary = outcome.report["validation_summary"]
        logger.info(
            "%d annotations; %d evaluation-ready; %d excluded; %d invalid",
            summary["total"],
            summary["evaluation_ready"],
            summary["excluded"],
            summary["invalid"],
        )
        if not summary["evaluation_ready"]:
            logger.warning("No evaluation-ready annotations; no usable gold labels yet")
        return 1 if summary["invalid"] or outcome.report["corpus_errors"] else 0
    except (OSError, ValueError, KeyError, TypeError) as exc:
        logger.error("Annotation validation failed: %s", exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
