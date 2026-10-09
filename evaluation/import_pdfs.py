"""Evaluation-only local PDF registration CLI; no fetching, indexing or analysis."""

import argparse
import logging
from pathlib import Path

from evaluation.local_corpus import (
    CORPUS_VERSION,
    MetadataFile,
    load_corpus,
    register_directory,
    verify_corpus,
)

logger = logging.getLogger(__name__)


def main(argv: list[str] | None = None) -> int:
    cli = argparse.ArgumentParser(description="Register manually downloaded procurement PDFs")
    cli.add_argument("input_dir", type=Path, nargs="?", help="Directory of top-level PDF files")
    cli.add_argument("--corpus-dir", type=Path, default=Path("data/evaluation/real-pdf-1"))
    cli.add_argument("--corpus-version", default=CORPUS_VERSION)
    cli.add_argument(
        "--metadata", type=Path, help="JSON object with defaults and per-filename files metadata"
    )
    cli.add_argument(
        "--tender-id", help="Evaluation tender group, shared unless overridden per file"
    )
    cli.add_argument(
        "--ted-publication-id", help="Optional official publication number; no API lookup"
    )
    cli.add_argument(
        "--verify",
        action="store_true",
        help="Check registered hashes without importing or rewriting",
    )
    args = cli.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    try:
        if args.verify:
            if args.input_dir or args.metadata or args.tender_id or args.ted_publication_id:
                raise ValueError(
                    "--verify only accepts corpus directory/version, not import arguments"
                )
            if not (args.corpus_dir / "manifest.json").is_file():
                raise ValueError("No corpus manifest exists to verify")
            corpus = load_corpus(args.corpus_dir, args.corpus_version)
            if not corpus["documents"]:
                raise ValueError("Corpus has no registered PDFs to verify")
            errors = verify_corpus(args.corpus_dir, corpus)
            for error in errors:
                logger.error("%s", error)
            logger.info(
                "Verified %d registered PDFs; %d integrity failures",
                len(corpus["documents"]),
                len(errors),
            )
            return 1 if errors else 0
        if args.input_dir is None:
            raise ValueError("input_dir is required unless --verify is used")
        metadata = (
            MetadataFile.model_validate_json(args.metadata.read_text(encoding="utf-8"))
            if args.metadata
            else MetadataFile()
        )
        defaults = {
            key: value
            for key, value in {
                "tender_id": args.tender_id,
                "ted_publication_id": args.ted_publication_id,
            }.items()
            if value is not None
        }
        run = register_directory(
            args.input_dir,
            args.corpus_dir,
            corpus_version=args.corpus_version,
            metadata=metadata,
            defaults=defaults,
        )
        for result in run["results"]:
            if result["status"] == "rejected":
                logger.error("%s: %s", result["original_filename"], result["error"])
            else:
                logger.info(
                    "%s: %s (%s)",
                    result["original_filename"],
                    result["status"],
                    result["document_id"],
                )
                for flag in result["flags"]:
                    logger.warning("%s: %s", result["original_filename"], flag)
        return 1 if run["status"] == "partial" else 0
    except (OSError, ValueError, KeyError, TypeError) as exc:
        logger.error("Corpus registration failed: %s", exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
