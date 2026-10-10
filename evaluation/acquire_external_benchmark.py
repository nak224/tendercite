"""Evaluation-only public EU Tenders QA acquisition CLI; CI must use mocked sources."""

import argparse
import logging
from pathlib import Path

from evaluation import external_benchmark as pilot

logger = logging.getLogger(__name__)


def main(argv: list[str] | None = None) -> int:
    cli = argparse.ArgumentParser(description="Acquire/validate one external procurement QA family")
    cli.add_argument("--revision", help="Dataset ref or commit SHA, resolved to immutable SHA")
    cli.add_argument("--family", help="One exact upstream family name")
    cli.add_argument(
        "--download-pdfs", action="store_true", help="Opt in to PDFs for --family only"
    )
    cli.add_argument("--output-dir", type=Path, default=pilot.EXTERNAL_ROOT / "eu-tenders-qa-pilot")
    cli.add_argument(
        "--validate-only", action="store_true", help="Recheck local files without networking"
    )
    args = cli.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    try:
        if args.validate_only:
            if args.revision or args.family or args.download_pdfs:
                raise ValueError("--validate-only accepts only --output-dir")
            report = pilot.validate_local(args.output_dir)
        else:
            if not args.revision:
                raise ValueError("--revision is required for acquisition")
            report = pilot.acquire(
                args.revision, args.output_dir, family=args.family, download_pdfs=args.download_pdfs
            )
        for issue in report.get("issues", report.get("failures", [])):
            logger.error("%s", issue)
        metadata = report.get("metadata", {})
        selected = report.get("family_validation", {})
        logger.info(
            "%s: %s metadata records; %s family questions; %s usable document references; "
            "0 exact source-span ground truth; no model evaluation",
            report["status"],
            metadata.get("records"),
            selected.get("questions"),
            selected.get("usable_document_reference_questions"),
        )
        return 0 if report["status"] == "validated" else 1
    except (OSError, ValueError, KeyError, TypeError) as exc:
        logger.error("External benchmark pilot failed: %s", exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
