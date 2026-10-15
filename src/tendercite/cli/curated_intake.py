"""Offline manual candidate intake; explicit persistence, private source-bearing inspection."""

import json
import sqlite3
import sys

from pydantic import ValidationError

from tendercite.cli.capability_workflow import _read_only_repository
from tendercite.cli.import_capabilities import SafeParser
from tendercite.domain.capability_mapping_records import CapabilityMappingError
from tendercite.domain.curated_intake import (
    MAX_INTAKE_BYTES,
    CuratedIntakeError,
    CuratedTenderIntake,
    IntakeErrorCode,
)
from tendercite.repositories.sqlite import SqliteRepository
from tendercite.services.capabilities import (
    CapabilityError,
    CapabilityErrorCode,
    private_artifact_path,
    read_local_model,
    write_private_json,
)


def _run(args):
    if args.command == "commit" and not args.confirm_manual_intake:
        raise CuratedIntakeError(IntakeErrorCode.CONFIRMATION_REQUIRED)
    output = (
        private_artifact_path(args.output, subdirectory="data/curated")
        if args.command == "inspect" and args.output
        else None
    )
    intake = None
    if args.command != "inspect":
        path = private_artifact_path(args.input, subdirectory="data/curated")
        try:
            intake = read_local_model(path, CuratedTenderIntake, max_bytes=MAX_INTAKE_BYTES)
        except CapabilityError as error:
            if error.code == CapabilityErrorCode.INVALID_CONTRACT:
                raise CuratedIntakeError(IntakeErrorCode.INVALID_INPUT) from None
            raise
    repository = _read_only_repository(args.database)
    if args.command == "validate":
        result = repository.validate_curated_intake(intake)
    elif args.command == "commit":
        # Existing DB only: no schema initialization and no creation after path deletion.
        writer = SqliteRepository(repository.database_path, initialize=False)
        result = writer.commit_curated_intake(intake)
    else:
        run, result = repository.inspect_curated_intake(args.analysis_id)
        if output is not None:
            write_private_json(output, run)
    return result.model_dump(mode="json")


def main(argv=None):
    parser = SafeParser(description=__doc__)
    parser.add_argument("--database", help="Existing local .db; defaults to TENDERCITE_DATA_DIR")
    commands = parser.add_subparsers(dest="command", required=True, parser_class=SafeParser)
    commands.add_parser("validate").add_argument("--input", required=True)
    commit = commands.add_parser("commit")
    commit.add_argument("--input", required=True)
    commit.add_argument("--confirm-manual-intake", action="store_true")
    inspect = commands.add_parser("inspect")
    inspect.add_argument("--analysis-id", required=True)
    inspect.add_argument(
        "--output", help="Explicit new private JSON containing original intake sources"
    )
    try:
        result = _run(parser.parse_args(argv))
    except (CuratedIntakeError, CapabilityError, CapabilityMappingError) as error:
        print(json.dumps({"status": "ERROR", "error": error.code.value}), file=sys.stderr)
        return 2
    except (ValidationError, ValueError, RecursionError, OverflowError):
        print(json.dumps({"status": "ERROR", "error": "INVALID_CURATED_INTAKE"}), file=sys.stderr)
        return 2
    except (OSError, sqlite3.Error):
        print(json.dumps({"status": "ERROR", "error": "LOCAL_OPERATION_FAILED"}), file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
