"""Validate/import a synthetic local snapshot; inspect metadata or explicitly export by hash."""

import argparse
import json
import sqlite3
import sys
from dataclasses import asdict

from pydantic import ValidationError

from tendercite.domain.capabilities_v1 import CapabilitySnapshot
from tendercite.repositories.sqlite import SqliteRepository
from tendercite.services.capabilities import (
    CapabilityError,
    CapabilityErrorCode,
    read_capability_snapshot,
    write_capability_snapshot,
)


class SafeParser(argparse.ArgumentParser):
    def error(self, _message):
        # argparse's default error text can echo personnel-bearing arguments/paths.
        raise CapabilityError(CapabilityErrorCode.INVALID_ARGUMENTS)


def snapshot_summary(snapshot: CapabilitySnapshot) -> dict:
    return {
        "content_hash": snapshot.content_hash,
        "schema_version": snapshot.schema_version,
        "source_system": snapshot.source_system,
        "assessment_period": snapshot.assessment_period.model_dump(mode="json"),
        "member_count": len(snapshot.workforce_members),
        "assignment_count": sum(len(m.skills) for m in snapshot.workforce_members),
        "catalog_count": len(snapshot.skill_catalog),
    }


def _repository(*, read_only: bool = False) -> SqliteRepository:
    # Validation-only deliberately never loads application configuration or creates a DB.
    try:
        from tendercite.core.config import Settings

        settings = Settings()
    except (ValueError, OSError):
        raise CapabilityError(CapabilityErrorCode.CONFIGURATION_FAILED) from None
    if read_only and not settings.database_path.is_file():
        raise CapabilityError(CapabilityErrorCode.NOT_FOUND)
    return SqliteRepository(settings.database_path, read_only=read_only)


def _run(args) -> dict:
    if (
        (args.validate_only and not args.input)
        or (args.acknowledge_local_synthetic_data and (not args.input or args.validate_only))
        or (args.output and not args.get)
        or (args.limit != 50 and not args.list)
    ):
        raise CapabilityError(CapabilityErrorCode.INVALID_ARGUMENTS)
    if args.input:
        if not args.validate_only and not args.acknowledge_local_synthetic_data:
            raise CapabilityError(CapabilityErrorCode.ACKNOWLEDGEMENT_REQUIRED)
        snapshot = read_capability_snapshot(args.input)
        result = snapshot_summary(snapshot)
        if args.validate_only:
            return {"status": "VALIDATED", **result}
        imported = _repository().import_capability_snapshot(snapshot)
        return {
            "status": "INSERTED" if imported.inserted else "ALREADY_PRESENT",
            "imported_at": imported.metadata.imported_at.isoformat(),
            **result,
        }
    if args.list:
        if not 1 <= args.limit <= 100:
            raise CapabilityError(CapabilityErrorCode.INVALID_ARGUMENTS)
        rows = _repository(read_only=True).list_capability_snapshots(limit=args.limit)
        return {"status": "LISTED", "snapshots": [asdict(row) for row in rows]}
    SqliteRepository._check_capability_hash(args.get)
    snapshot = _repository(read_only=True).get_capability_snapshot(args.get)
    if snapshot is None:
        raise CapabilityError(CapabilityErrorCode.NOT_FOUND)
    if args.output:
        write_capability_snapshot(args.output, snapshot)
    return {"status": "EXPORTED" if args.output else "FOUND", **snapshot_summary(snapshot)}


def main(argv: list[str] | None = None) -> int:
    parser = SafeParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--input", help="Local .json snapshot path")
    action.add_argument("--list", action="store_true", help="Bounded metadata only")
    action.add_argument(
        "--get", metavar="CONTENT_HASH", help="Inspect one snapshot by verified hash"
    )
    parser.add_argument(
        "--validate-only", action="store_true", help="No database/configuration access"
    )
    parser.add_argument("--acknowledge-local-synthetic-data", action="store_true")
    parser.add_argument("--limit", type=int, default=50, help="Metadata list bound, 1–100")
    parser.add_argument(
        "--output", help="Explicit full export for --get, to a new local .json file"
    )
    try:
        result = _run(parser.parse_args(argv))
    except CapabilityError as exc:
        print(json.dumps({"status": "ERROR", "error": exc.code.value}), file=sys.stderr)
        return 3 if exc.code == CapabilityErrorCode.NOT_FOUND else 2
    except (sqlite3.Error, OSError, ValidationError):
        print(json.dumps({"status": "ERROR", "error": "DATABASE_FAILED"}), file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True, default=lambda value: value.isoformat()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
