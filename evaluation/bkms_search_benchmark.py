"""Opt-in offline measurement with normalized synthetic catalogs and disposable SQLite only."""

import argparse
import hashlib
import json
import os
import platform
import sqlite3
import statistics
import tempfile
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from time import perf_counter

import pydantic

from tendercite.domain.bkms import CatalogContents
from tendercite.domain.bkms_catalogs import MultiSearchInput
from tendercite.domain.bkms_deadlines import (
    DeadlineCatalog,
    DeadlineLoadInput,
    DeadlineNotice,
    LotDeadline,
    SourcePeriod,
)
from tendercite.domain.bkms_reference import BkmsSource, OriginalMetadata, notice_url
from tendercite.repositories.sqlite import SqliteRepository
from tendercite.services.bkms import search_catalogs


def digest(text):
    return hashlib.sha256(text.encode()).hexdigest()


def run(per_day=1000, days=7, repeats=5, enriched=False):
    if per_day not in (500, 1000, 2000) or not 1 <= days <= 7 or not 1 <= repeats <= 10:
        raise ValueError("Unsupported bounded benchmark configuration")
    with tempfile.TemporaryDirectory(prefix="tendercite-bkms-benchmark-") as directory:
        repo = SqliteRepository(Path(directory) / "synthetic.db")
        generated = datetime(2026, 11, 5, tzinfo=UTC)
        started = perf_counter()
        for d in range(days):
            day = date(2026, 10, 24) + timedelta(days=d)
            archive_hash = digest(f"synthetic normalized archive {d}")
            records = []
            for i in range(per_day):
                notice = 10000000 + (i if i % 10 == 0 else d * per_day + i)
                version = str(1 + d if i % 20 == 0 else 1)
                identity = f"{notice}-{version}"
                records.append(
                    BkmsSource(
                        external_id=identity,
                        original_url=notice_url(identity),
                        record_url=f"https://oeffentlichevergabe.de/api/notices/{notice}?format=ocds&noticeVersion={version}",
                        ocid=None,
                        export_day=day,
                        retrieved_at=generated,
                        archive_sha256=archive_hash,
                        record_sha256=digest(f"synthetic record {d}:{i}"),
                        original_metadata=OriginalMetadata(
                            title=f"Synthetic software procurement {i}",
                            buyer="Fictional buyer",
                            description=("Synthetic software services only. " * 40)[:1000],
                            published_at=f"{day}T08:00:00Z",
                            deadline=None,
                            tags=("award",) if i % 3 == 0 else ("tender",),
                        ),
                        mapping_warnings=("MISSING_DEADLINE",),
                    )
                )
            summary = repo.save_bkms_catalog(
                CatalogContents(
                    export_day=day,
                    archive_sha256=archive_hash,
                    retrieved_at=generated,
                    records=tuple(records),
                )
            )
            if enriched:
                rows = tuple(
                    DeadlineNotice(
                        external_id=r.external_id,
                        record_sha256=digest(f"synthetic deadline {d}:{i}"),
                        customization_id="eforms-de-2.1",
                        status="SINGLE",
                        warnings=(),
                        lots=(
                            LotDeadline(
                                lot_id="LOT-0001",
                                status="SINGLE",
                                periods=(
                                    SourcePeriod(
                                        dates=("2026-12-04+01:00",), times=("10:00:00+01:00",)
                                    ),
                                ),
                            ),
                        ),
                    )
                    for i, r in enumerate(records)
                )
                repo.save_bkms_deadlines(
                    DeadlineCatalog(
                        catalog_id=summary.catalog_id,
                        export_day=day,
                        archive_sha256=digest(f"synthetic eforms {d}"),
                        retrieved_at=generated,
                        records=rows,
                    ),
                    DeadlineLoadInput(catalog_id=summary.catalog_id),
                )
        setup_seconds = perf_counter() - started
        coverage = repo.get_bkms_catalogs()[1]
        cases = []
        for keyword, category in (
            ("Software", "ALL"),
            ("Software", "AWARD"),
            ("UnmatchedPhrase", "ALL"),
        ):
            request = MultiSearchInput(
                collection_id=coverage.collection_id,
                keyword=keyword,
                notice_category=category,
                limit=20,
            )
            search_catalogs(
                repo, request
            )  # One explicit warmup; measurements include SQLite decode/validation.
            timings = []
            for _ in range(repeats):
                start = perf_counter()
                result = search_catalogs(repo, request)
                timings.append((perf_counter() - start) * 1000)
            cases.append(
                {
                    "keyword": keyword,
                    "category": category,
                    "matches": result.total_count,
                    "returned": len(result.items),
                    "milliseconds": timings,
                    "median_ms": statistics.median(timings),
                    "min_ms": min(timings),
                    "max_ms": max(timings),
                }
            )
        cpu = platform.processor() or "not reported"
        if Path("/proc/cpuinfo").exists():
            cpu = next(
                (
                    line.partition(":")[2].strip()
                    for line in Path("/proc/cpuinfo").read_text().splitlines()
                    if line.startswith("model name")
                ),
                cpu,
            )
        return {
            "schema_version": "bkms-search-benchmark-1",
            "synthetic": True,
            "measured_at": datetime.now(UTC).isoformat(),
            "python": platform.python_version(),
            "pydantic": pydantic.__version__,
            "sqlite": sqlite3.sqlite_version,
            "platform": platform.platform(),
            "cpu": cpu,
            "logical_cpus": os.cpu_count(),
            "days": days,
            "records_per_day": per_day,
            "stored_records": days * per_day,
            "description_characters": 1000,
            "active_catalog_bytes": coverage.stored_bytes,
            "with_enrichment": enriched,
            "setup_seconds": setup_seconds,
            "repeats": repeats,
            "warmups_per_case": 1,
            "page": 1,
            "limit": 20,
            "include_earlier_versions": False,
            "cases": cases,
        }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--notices-per-day", type=int, choices=(500, 1000, 2000), default=1000)
    parser.add_argument("--days", type=int, choices=range(1, 8), default=7)
    parser.add_argument("--repeats", type=int, choices=range(1, 11), default=5)
    parser.add_argument("--with-enrichment", action="store_true")
    args = parser.parse_args()
    print(
        json.dumps(
            run(args.notices_per_day, args.days, args.repeats, args.with_enrichment), indent=2
        )
    )


if __name__ == "__main__":
    main()
