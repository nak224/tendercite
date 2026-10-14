"""Local operator-controlled mapping workflow; no provider configuration or personnel output."""

import json
import sqlite3
import sys
from pathlib import Path
from uuid import UUID

from pydantic import ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict

from tendercite.cli.import_capabilities import SafeParser
from tendercite.domain.capability_mapping_records import (
    CapabilityMappingError,
    MappingApprovalInput,
    MappingDraftInput,
    MappingErrorCode,
    MappingRevisionInput,
    MappingRevocationInput,
)
from tendercite.domain.capability_matching import CapabilityMatchingRequest
from tendercite.repositories.sqlite import SqliteRepository
from tendercite.services.capabilities import (
    CapabilityError,
    _nonfinite,
    _unique_object,
    read_local_json_bytes,
)
from tendercite.services.capability_matching import CapabilityMatchingError
from tendercite.services.persistent_capability_matching import match_persisted_capabilities


class LocalSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="TENDERCITE_", env_file=".env", extra="ignore")
    data_dir: Path = Path("data")


def _input(path, contract):
    raw = read_local_json_bytes(path, max_bytes=65536)
    try:
        body = json.loads(
            raw.decode("utf-8"), object_pairs_hook=_unique_object, parse_constant=_nonfinite
        )
        return contract.model_validate_json(json.dumps(body, ensure_ascii=False, allow_nan=False))
    except (ValueError, UnicodeError, RecursionError, OverflowError):
        raise CapabilityMappingError(MappingErrorCode.INVALID_INPUT) from None


def _repository(database, *, write):
    path = Path(database) if database else LocalSettings().data_dir / "tendercite.db"
    # SQLite paths must be local. Explicit URLs and remote-share paths are never opened.
    text = str(path)
    if text.startswith(("//", "\\\\")) or "://" in text or path.suffix != ".db":
        raise CapabilityMappingError(MappingErrorCode.INVALID_INPUT)
    if not path.is_file():
        raise CapabilityMappingError(MappingErrorCode.NOT_FOUND)
    return SqliteRepository(path, read_only=not write)


def _run(args):
    operations = {
        "create": (MappingDraftInput, "create_capability_mapping_draft"),
        "revise": (MappingRevisionInput, "revise_capability_mapping"),
        "approve": (MappingApprovalInput, "approve_capability_mapping"),
        "revoke": (MappingRevocationInput, "revoke_capability_mapping"),
    }
    request = (
        _input(args.input, operations[args.command][0]) if args.command in operations else None
    )
    if args.command == "match":
        request = _input(args.input, CapabilityMatchingRequest)
        if request.mappings:
            raise CapabilityMappingError(MappingErrorCode.INVALID_INPUT)
    mapping_id = UUID(args.mapping_id) if args.command in ("inspect", "history") else None
    if args.command == "list" and not 1 <= args.limit <= 100:
        raise CapabilityMappingError(MappingErrorCode.INVALID_INPUT)
    repo = _repository(args.database, write=args.command in operations)
    if args.command in operations:
        record = getattr(repo, operations[args.command][1])(request)
        return {"status": record.status.value, "mapping": record.model_dump(mode="json")}
    if args.command == "list":
        rows = repo.list_capability_mappings(
            analysis_id=args.analysis_id, snapshot_hash=args.snapshot_hash, limit=args.limit
        )
        return {"status": "LISTED", "mappings": [m.model_dump(mode="json") for m in rows]}
    if args.command == "review-state":
        binding = repo.get_mapping_review_binding(
            args.analysis_id, args.finding_id, args.snapshot_hash
        )
        return {"status": "FOUND", "review_binding": binding.model_dump(mode="json")}
    if args.command in ("inspect", "history"):
        mapping = repo.get_capability_mapping(mapping_id)
        if mapping is None:
            raise CapabilityMappingError(MappingErrorCode.NOT_FOUND)
        if args.command == "inspect":
            return {"status": mapping.status.value, "mapping": mapping.model_dump(mode="json")}
        return {
            "status": "HISTORY",
            "revisions": [
                m.model_dump(mode="json") for m in repo.capability_mapping_history(mapping_id)
            ],
        }
    result = match_persisted_capabilities(
        analysis_id=request.analysis_id,
        snapshot_hash=request.snapshot_hash,
        finding_ids=request.finding_ids,
        repository=repo,
    )
    return {
        "status": "MATCHED",
        "schema_version": result.schema_version,
        "algorithm_version": result.algorithm_version,
        "analysis_id": result.analysis_id,
        "snapshot_hash": result.snapshot_hash,
        "qualification": result.qualification,
        "capacity_evaluation": result.capacity_evaluation,
        # The Python service returns the complete 4.2a result. CLI stdout deliberately
        # aggregates individuals without exposing person UUIDs, assignments or capacity records.
        "findings": [
            dict(
                finding_id=row.finding_id,
                coverage=row.coverage.value,
                non_evaluation_reasons=[r.value for r in row.non_evaluation_reasons],
                individual_coverage_counts={
                    status: sum(c.coverage.value == status for c in row.candidates)
                    for status in sorted({c.coverage.value for c in row.candidates})
                },
            )
            for row in result.findings
        ],
    }


def main(argv=None):
    parser = SafeParser(description=__doc__)
    parser.add_argument("--database", help="Existing local .db; defaults to TENDERCITE_DATA_DIR")
    commands = parser.add_subparsers(dest="command", required=True, parser_class=SafeParser)
    for name in ("create", "revise", "approve", "revoke", "match"):
        commands.add_parser(name).add_argument(
            "--input", required=True, help="Versioned local JSON"
        )
    for name in ("inspect", "history"):
        commands.add_parser(name).add_argument("--mapping-id", required=True)
    listing = commands.add_parser("list")
    listing.add_argument("--analysis-id")
    listing.add_argument("--snapshot-hash")
    listing.add_argument("--limit", type=int, default=50)
    review = commands.add_parser("review-state")
    for name in ("analysis-id", "finding-id", "snapshot-hash"):
        review.add_argument("--" + name, required=True)
    try:
        result = _run(parser.parse_args(argv))
    except (CapabilityMappingError, CapabilityError, CapabilityMatchingError) as error:
        print(json.dumps({"status": "ERROR", "error": error.code.value}), file=sys.stderr)
        return 2
    except (ValidationError, ValueError):
        print(json.dumps({"status": "ERROR", "error": "INVALID_MAPPING_INPUT"}), file=sys.stderr)
        return 2
    except (sqlite3.Error, OSError):
        print(json.dumps({"status": "ERROR", "error": "DATABASE_FAILED"}), file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
