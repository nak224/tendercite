"""Executable synthetic workflow; all approvals/reviews here are simulations."""

import argparse
import json
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import UUID

from examples.persistent_capability_mappings import seed_demo
from tendercite.cli.capability_workflow import private_workflow_path
from tendercite.domain.capability_mapping_records import (
    MappingApprovalInput,
    MappingRevisionInput,
    MappingRevocationInput,
)
from tendercite.domain.capability_workflow import AssessmentSelection, MappingSelection
from tendercite.domain.models import ReviewedValue
from tendercite.domain.review import ReviewRequest
from tendercite.services.capabilities import write_private_json


def run_demo(directory: Path):
    private_workflow_path(directory / "selection.json")
    repo, snapshot, finding = seed_demo(directory)

    def invoke(module, *args):
        # Real local subprocesses. Capture the existing lifecycle CLI's reviewed
        # text internally; the demo console exposes only counts and metadata.
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "tendercite.cli." + module,
                "--database",
                str(repo.database_path),
                *map(str, args),
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )
        if result.returncode:
            raise RuntimeError("SYNTHETIC_WORKFLOW_FAILED")
        return json.loads(result.stdout)

    def write(name, model):
        path = directory / name
        write_private_json(path, model)
        return path

    identity = dict(analysis_id=finding.analysis_run_id, snapshot_hash=snapshot.content_hash)
    invoke(
        "capability_workflow",
        "overview",
        "--analysis-id",
        identity["analysis_id"],
        "--snapshot-hash",
        identity["snapshot_hash"],
        "--output",
        directory / "overview.json",
    )
    selection = write(
        "selection.json",
        MappingSelection(
            **identity,
            finding_id=finding.id,
            operator="ALL_OF",
            skill_ids=(UUID("00000000-0000-0000-0000-000000000065"),),
            rationale="Explicit synthetic association of PostgreSQL catalog UUID",
            operator_alias="demo_operator",
        ),
    )
    draft = directory / "draft.json"
    invoke("capability_workflow", "prepare", "--input", selection, "--output", draft)
    created = invoke("capability_mappings", "create", "--input", draft)
    record = repo.get_capability_mapping(created["mapping"]["mapping_id"]).record
    assessment = write(
        "assessment-selection.json", AssessmentSelection(**identity, finding_ids=(finding.id,))
    )
    invoke(
        "capability_workflow",
        "report",
        "--input",
        assessment,
        "--output",
        directory / "draft-assessment.json",
    )
    approval = write(
        "approval.json",
        MappingApprovalInput(
            mapping_id=record.mapping_id,
            expected_revision=record.revision,
            expected_review_event_id=record.review_binding.review_event_id,
            expected_review_digest=record.review_binding.digest,
            rationale="Explicit simulated operator approval; qualification remains unverified",
            operator_alias="demo_operator",
        ),
    )
    invoke("capability_mappings", "approve", "--input", approval)
    current = invoke(
        "capability_workflow",
        "report",
        "--input",
        assessment,
        "--output",
        directory / "approved-assessment.json",
    )
    # A changed interpretation makes even the previously approved UUID association stale.
    repo.add_review(
        finding.id,
        ReviewRequest(
            action="MODIFY",
            reviewed_value=ReviewedValue(
                statement="At least five years of PostgreSQL experience are required.",
                category="MUST",
                requirement_type="TECHNICAL",
            ),
            comment="Simulated review only; no experience duration is verified",
        ),
    )
    stale = invoke(
        "capability_workflow",
        "report",
        "--input",
        assessment,
        "--output",
        directory / "stale-assessment.json",
    )
    binding = repo.get_mapping_review_binding(**identity, finding_id=finding.id)
    write(
        "revision.json",
        MappingRevisionInput(
            mapping_id=record.mapping_id,
            expected_revision=2,
            expected_review_event_id=binding.review_event_id,
            expected_review_digest=binding.digest,
            operator="ALL_OF",
            skill_ids=record.skill_ids,
            rationale="Explicit reconsideration of changed interpretation; duration unverified",
        ),
    )
    write(
        "reapproval.json",
        MappingApprovalInput(
            mapping_id=record.mapping_id,
            expected_revision=3,
            expected_review_event_id=binding.review_event_id,
            expected_review_digest=binding.digest,
            rationale="Explicit approval of revised association only",
        ),
    )
    write(
        "revocation.json",
        MappingRevocationInput(
            mapping_id=record.mapping_id,
            expected_revision=4,
            rationale="Explicit withdrawal of the revised association",
        ),
    )
    return dict(
        simulation_only=True,
        analysis_id=finding.analysis_run_id,
        mapping_id=str(record.mapping_id),
        snapshot_hash=snapshot.content_hash,
        approved=current["summary"],
        after_changed_review=stale["summary"],
        history_statuses=[
            r.status.value for r in repo.capability_mapping_history(record.mapping_id)
        ],
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, help="Fresh private local directory")
    args = parser.parse_args()
    if args.directory:
        result = run_demo(args.directory)
    else:
        with TemporaryDirectory(prefix="tendercite-workflow-demo-") as temporary:
            result = run_demo(Path(temporary))
    print(json.dumps(result, sort_keys=True))
