"""Run explicitly: python -m evaluation.acquire_ted --help. Never invoked by normal CI."""

import argparse
import json
import logging
from dataclasses import asdict
from datetime import date
from pathlib import Path

from evaluation.ted_acquisition import (
    DATASET_VERSION,
    acquire_notice,
    load_manifest,
    save_manifest,
    timestamp,
    verified_cache,
)
from tendercite.services.ted import (
    LANGUAGES,
    MAX_NOTICES,
    SearchFilters,
    TedClient,
    TedError,
    language_code,
    publication_id,
)

logger = logging.getLogger(__name__)


def parser() -> argparse.ArgumentParser:
    cli = argparse.ArgumentParser(description="Acquire official TED notices for manual evaluation")
    cli.add_argument("--output-dir", type=Path, default=Path("data/evaluation/ted"))
    cli.add_argument("--dataset-version", default=DATASET_VERSION)
    cli.add_argument("--country", default="DEU", help="Place of performance: three-letter TED code")
    cli.add_argument("--date-from", type=date.fromisoformat)
    cli.add_argument("--date-to", type=date.fromisoformat)
    cli.add_argument("--cpv", default="72", help="Two to eight digits; shorter codes are prefixes")
    cli.add_argument("--keyword", help="Literal full-text phrase, not an expert query")
    cli.add_argument("--limit", type=int, default=20, help="1–50 results, one page only")
    cli.add_argument(
        "--include-other-forms", action="store_true", help="Also consider award/planning notices"
    )
    cli.add_argument(
        "--language", default="deu", help="Required original language (ISO 639-2/639-1)"
    )
    cli.add_argument(
        "--notice-id", action="append", default=[], help="Fixed publication ID; repeatable, max 50"
    )
    cli.add_argument(
        "--refresh",
        action="store_true",
        help="Re-download fixed IDs; retain content hash deduplication",
    )
    cli.add_argument(
        "--shortlist-id",
        action="append",
        default=[],
        help="Explicit manually selected ID after reading PDFs",
    )
    cli.add_argument(
        "--summary",
        type=Path,
        help="Write a metadata-only copy for candidate review (no PDF/XML bytes)",
    )
    cli.add_argument(
        "--smoke", action="store_true", help="Search/validate API metadata only; no downloads"
    )
    return cli


def run(args: argparse.Namespace, client: TedClient) -> int:
    filters = SearchFilters(
        args.country,
        args.date_from,
        args.date_to,
        args.cpv,
        args.keyword,
        args.limit,
        not args.include_other_forms,
    )
    query = filters.query()  # Validate even in fixed-ID mode.
    language = language_code(args.language)
    if language not in LANGUAGES:
        raise ValueError("language must identify an EU official language")
    ids = list(dict.fromkeys(publication_id(value) for value in args.notice_id))
    if len(ids) > MAX_NOTICES:
        raise ValueError(f"At most {MAX_NOTICES} fixed publication IDs are supported")
    manifest = load_manifest(args.output_dir, args.dataset_version)
    previous = {notice["publication_id"]: notice for notice in manifest["notices"]}
    failures = []
    current = []
    queries = [f'publication-number = "{value}"' for value in ids] if ids else [query]
    run_record = {
        "started_at": timestamp(),
        "mode": "fixed_ids" if ids else "search",
        "queries": queries,
        "filters": asdict(filters),
        "preferred_original_language": language,
        "fixed_ids": ids,
        "smoke": args.smoke,
        "refresh": args.refresh,
        "status": "running",
        "failures": failures,
    }
    # Dates become JSON strings, rather than losing the exact effective query configuration.
    for key in ("date_from", "date_to"):
        value = run_record["filters"][key]
        run_record["filters"][key] = value.isoformat() if value else None
    if ids:
        for notice_id in ids:
            try:
                current.append(client.lookup(notice_id))
            except TedError as exc:
                failures.append(str(exc))
                logger.error("%s", exc)
    else:
        try:
            result = client.search(filters)
            run_record["total_notice_count"] = result.total_notice_count
            current = list(result.notices)
        except TedError as exc:
            failures.append(str(exc))
            logger.error("%s", exc)
    # Competition notices first when other forms are explicitly allowed; stable within each group.
    current.sort(key=lambda notice: notice.form_type != "competition")
    seen = set()
    for notice in current:
        notice_id = notice.publication_id
        if notice_id in seen:
            continue
        seen.add(notice_id)
        if args.smoke:
            logger.info(
                "API returned %s; official languages: %s", notice_id, notice.official_languages
            )
            continue
        existing = previous.get(notice_id)
        if (
            not args.refresh
            and existing
            and set(notice.official_languages) == set(existing["official_languages"])
            and language in notice.official_languages
            and verified_cache(existing, args.output_dir)
        ):
            logger.info("%s: using checksum-verified local acquisition", notice_id)
            continue
        previous[notice_id] = acquire_notice(client, notice, args.output_dir, language=language)
    manifest["notices"] = list(previous.values())
    manifest["shortlist"] = [
        value for value in manifest["shortlist"] if previous[value].get("shortlist_eligible")
    ]
    for value in args.shortlist_id:
        notice_id = publication_id(value)
        if notice_id not in previous or not previous[notice_id].get("shortlist_eligible"):
            message = f"Shortlist ID {notice_id} must have passed PDF/original-language checks"
            failures.append(message)
            logger.error("%s", message)
            continue
        previous[notice_id]["manual_review"] = "selected_for_annotation"
        if notice_id not in manifest["shortlist"]:
            manifest["shortlist"].append(notice_id)
    attempted = [] if args.smoke else [previous[value] for value in seen if value in previous]
    document_failure = any(
        notice["status"]
        in {"unusable_pdf", "pdf_not_obtained", "language_conflict", "skipped_unknown_language"}
        or notice.get("failures")
        for notice in attempted
    )
    acquired = sum(notice.get("shortlist_eligible", False) for notice in attempted)
    run_record.update(
        {
            "finished_at": timestamp(),
            "returned_publication_ids": sorted(seen),
            "status": "failed" if failures else "partial" if document_failure else "completed",
            "usable_pdf_count": acquired,
            "document_failures": {
                notice["publication_id"]: notice["failures"]
                for notice in attempted
                if notice.get("failures")
            },
        }
    )
    manifest["runs"].append(run_record)
    save_manifest(args.output_dir, manifest)
    if args.summary:
        if args.summary.suffix.lower() != ".json":
            raise ValueError("summary must be a JSON metadata file")
        args.summary.parent.mkdir(parents=True, exist_ok=True)
        args.summary.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")
    logger.info(
        "TED acquisition: %d unique metadata records; %d usable PDFs; %d API failures",
        len(seen),
        acquired,
        len(failures),
    )
    return 1 if failures or document_failure else 0


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    args = parser().parse_args(argv)
    try:
        with TedClient() as client:
            return run(args, client)
    except (ValueError, OSError, KeyError, json.JSONDecodeError) as exc:
        logger.error("Acquisition failed: %s", exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
