"""Private local exports with metadata-only console summaries; no networking."""

import json
import os
import sqlite3
import sys
from pathlib import Path

from pydantic import ValidationError

from tendercite.cli.capability_mappings import _input, _repository
from tendercite.cli.import_capabilities import SafeParser
from tendercite.domain.capability_mapping_records import CapabilityMappingError
from tendercite.domain.capability_workflow import AssessmentSelection, MappingSelection
from tendercite.services.capabilities import (
    CapabilityError,
    CapabilityErrorCode,
    local_json_path,
    write_private_json,
)
from tendercite.services.capability_matching import CapabilityMatchingError
from tendercite.services.capability_workflow import (
    assessment_overview,
    assessment_report,
    prepare_mapping_draft,
)

CHECKOUT = Path(__file__).absolute().parents[3]


def private_workflow_path(value):
    """Checkout artifacts belong only in Git-ignored, Docker-excluded private data.

    Outside a checkout, explicit local destinations are allowed. Other Git checkouts
    are rejected rather than assuming their ignore/build-context rules are safe.
    """
    path = local_json_path(value)
    for parent in path.parents:
        if (parent / ".git").exists():
            if parent != CHECKOUT or not path.is_relative_to(parent / "data/capabilities/workflow"):
                raise CapabilityError(CapabilityErrorCode.UNSAFE_INPUT)
    # Source installs without a .git directory still have a Docker build context.
    if (CHECKOUT / "Dockerfile").exists() and path.is_relative_to(CHECKOUT):
        if not path.is_relative_to(CHECKOUT / "data/capabilities/workflow"):
            raise CapabilityError(CapabilityErrorCode.UNSAFE_INPUT)
    return path


def _read_only_repository(database):
    repository = _repository(database, write=False)
    # Existing SQLite is trusted local application state, never a remote or linked DB.
    path = Path(os.path.abspath(repository.database_path))
    if ".." in repository.database_path.parts:
        raise CapabilityError(CapabilityErrorCode.UNSAFE_INPUT)
    for component in (path, *path.parents):
        if component.is_symlink() or component.is_junction():
            raise CapabilityError(CapabilityErrorCode.UNSAFE_INPUT)
    return repository


def _run(args):
    output = private_workflow_path(args.output)
    request = None
    if args.command != "overview":
        request = _input(
            private_workflow_path(args.input),
            MappingSelection if args.command == "prepare" else AssessmentSelection,
        )
    repository = _read_only_repository(args.database)
    if args.command == "overview":
        artifact = assessment_overview(
            analysis_id=args.analysis_id, snapshot_hash=args.snapshot_hash, repository=repository
        )
        summary = dict(
            finding_count=len(artifact.findings),
            review_source_eligible_count=sum(
                f.mapping_preparation_eligible for f in artifact.findings
            ),
            catalog_skill_count=len(artifact.snapshot.skill_catalog),
            workforce_member_count=artifact.snapshot.workforce_member_count,
        )
    elif args.command == "prepare":
        artifact = prepare_mapping_draft(request, repository=repository)
        summary = {
            "finding_id": artifact.finding_id,
            "selected_skill_count": len(artifact.skill_ids),
        }
    else:
        artifact = assessment_report(request, repository=repository)
        summary = artifact.summary.model_dump(mode="json")
    write_private_json(output, artifact)
    return dict(
        status="EXPORTED",
        schema_version=artifact.schema_version,
        analysis_id=artifact.analysis_id,
        snapshot_hash=artifact.snapshot_hash,
        summary=summary,
    )


def main(argv=None):
    parser = SafeParser(description=__doc__)
    parser.add_argument("--database", help="Existing local .db; defaults to TENDERCITE_DATA_DIR")
    commands = parser.add_subparsers(dest="command", required=True, parser_class=SafeParser)
    overview = commands.add_parser("overview")
    overview.add_argument("--analysis-id", required=True)
    overview.add_argument("--snapshot-hash", required=True)
    for command in ("overview", "prepare", "report"):
        subparser = overview if command == "overview" else commands.add_parser(command)
        if command != "overview":
            subparser.add_argument(
                "--input", required=True, help="Private versioned selection JSON"
            )
        subparser.add_argument("--output", required=True, help="New private local JSON file")
    try:
        result = _run(parser.parse_args(argv))
    except (CapabilityMappingError, CapabilityError, CapabilityMatchingError) as error:
        print(json.dumps({"status": "ERROR", "error": error.code.value}), file=sys.stderr)
        return 2
    except (ValidationError, ValueError, RecursionError, OverflowError):
        print(json.dumps({"status": "ERROR", "error": "INVALID_WORKFLOW_INPUT"}), file=sys.stderr)
        return 2
    except (sqlite3.Error, OSError):
        print(json.dumps({"status": "ERROR", "error": "LOCAL_OPERATION_FAILED"}), file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
