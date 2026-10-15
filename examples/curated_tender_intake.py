"""Self-authored PDF intake and simulated review/approval; no genuine human verification."""

import json
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from uuid import UUID

from fastapi.testclient import TestClient

from evaluation.generate import make_pdf
from tendercite.api.dependencies import get_repository
from tendercite.cli.capability_mappings import main as mapping_cli
from tendercite.cli.capability_workflow import main as workflow_cli
from tendercite.cli.curated_intake import main as intake_cli
from tendercite.cli.import_capabilities import SafeParser
from tendercite.core.config import settings
from tendercite.domain.capability_mapping_records import MappingApprovalInput
from tendercite.domain.capability_workflow import AssessmentSelection, MappingSelection
from tendercite.domain.curated_intake import CuratedTenderIntake
from tendercite.domain.models import DocumentRead, PageRead, ReviewedValue
from tendercite.domain.review import ReviewRequest
from tendercite.main import app
from tendercite.repositories.sqlite import SqliteRepository
from tendercite.services.capabilities import (
    private_artifact_path,
    read_capability_snapshot,
    write_private_json,
)

QUOTE = "The contractor must provide PostgreSQL development experience."


def run_demo(directory: Path):
    directory = private_artifact_path(directory / "intake.json", subdirectory="data/curated").parent
    for component in (directory, *directory.parents):
        if component.is_symlink() or component.is_junction():
            raise ValueError("Use a private unlinked synthetic directory")
    if directory.exists() and any(directory.iterdir()):
        raise ValueError("Use a fresh synthetic directory")
    directory.mkdir(parents=True, mode=0o700, exist_ok=True)
    repo = SqliteRepository(directory / "tendercite.db")
    overrides = app.dependency_overrides.copy()
    old_data, old_retrieval = settings.data_dir, settings.retrieval_enabled
    try:
        settings.data_dir, settings.retrieval_enabled = directory, False
        app.dependency_overrides[get_repository] = lambda: repo
        # Actual existing upload route, parser and chunker, in-process without a socket.
        with TestClient(app) as client:
            response = client.post(
                "/api/v1/documents",
                files={
                    "file": (
                        "self-authored.pdf",
                        make_pdf([QUOTE, "Offers must arrive before the stated deadline."]),
                        "application/pdf",
                    )
                },
            )
        if response.status_code != 201:
            raise RuntimeError("SYNTHETIC_DOCUMENT_INGESTION_FAILED")
        document = DocumentRead.model_validate(response.json())
    finally:
        settings.data_dir, settings.retrieval_enabled = old_data, old_retrieval
        app.dependency_overrides.clear()
        app.dependency_overrides.update(overrides)

    def write(name, model):
        path = directory / name
        write_private_json(path, model)
        return path

    def invoke(cli, *args):
        capture = StringIO()
        with redirect_stdout(capture):
            status = cli(["--database", str(repo.database_path), *map(str, args)])
        if status:
            raise RuntimeError("SYNTHETIC_INTAKE_WORKFLOW_FAILED")
        return json.loads(capture.getvalue())

    write("document.json", document)
    write("page-1.json", PageRead.model_validate(repo.get_page(document.id, 1)))
    # Explicitly authored fixture statement/quote, never extracted from parsed text.
    request = CuratedTenderIntake.model_validate_json(
        json.dumps(
            dict(
                schema_version="curated-tender-intake-1",
                source_origin="manual_operator_entry",
                operator_alias="demo_operator",
                purpose="Self-authored synthetic curated intake",
                documents=[
                    dict(
                        document_id=document.id,
                        expected_pdf_sha256=document.sha256,
                        source_note="Generated fixture, no third-party provenance",
                    )
                ],
                findings=[
                    dict(
                        item_id="postgresql",
                        statement=QUOTE,
                        category="MUST",
                        requirement_type="TECHNICAL",
                        evidence=[dict(document_id=document.id, page_number=1, quote=QUOTE)],
                        interpretation_notes="Manually authored candidate, not legal verification",
                    )
                ],
            )
        )
    )
    source = write("intake.json", request)
    validation = invoke(intake_cli, "validate", "--input", source)
    committed = invoke(intake_cli, "commit", "--input", source, "--confirm-manual-intake")
    run = repo.get_analysis(committed["analysis_id"])
    finding_id = run.findings[0].id
    invoke(
        intake_cli,
        "inspect",
        "--analysis-id",
        run.id,
        "--output",
        directory / "original-analysis.json",
    )
    fixture = (
        Path(__file__).absolute().parents[1]
        / "tests/fixtures/capabilities/tendercite-capability-v1.json"
    )
    snapshot = read_capability_snapshot(fixture)
    repo.import_capability_snapshot(snapshot)
    scope = dict(analysis_id=run.id, snapshot_hash=snapshot.content_hash)
    assessment = write(
        "assessment-selection.json", AssessmentSelection(**scope, finding_ids=(finding_id,))
    )
    before = invoke(
        workflow_cli,
        "report",
        "--input",
        assessment,
        "--output",
        directory / "unreviewed-assessment.json",
    )
    review = ReviewRequest(action="CONFIRM", comment="Synthetic human-review simulation only")
    write("review-request.json", review)
    repo.add_review(finding_id, review)
    invoke(
        workflow_cli,
        "overview",
        "--analysis-id",
        run.id,
        "--snapshot-hash",
        snapshot.content_hash,
        "--output",
        directory / "overview.json",
    )
    selection = write(
        "mapping-selection.json",
        MappingSelection(
            **scope,
            finding_id=finding_id,
            operator="ALL_OF",
            skill_ids=(UUID("00000000-0000-0000-0000-000000000065"),),
            rationale="Explicit synthetic PostgreSQL catalog UUID selection",
            operator_alias="demo_operator",
        ),
    )
    draft = directory / "mapping-draft.json"
    invoke(workflow_cli, "prepare", "--input", selection, "--output", draft)
    created = invoke(mapping_cli, "create", "--input", draft)
    record = repo.get_capability_mapping(created["mapping"]["mapping_id"]).record
    approval = write(
        "mapping-approval.json",
        MappingApprovalInput(
            mapping_id=record.mapping_id,
            expected_revision=record.revision,
            expected_review_event_id=record.review_binding.review_event_id,
            expected_review_digest=record.review_binding.digest,
            rationale="Explicit simulated approval of association only",
            operator_alias="demo_operator",
        ),
    )
    invoke(mapping_cli, "approve", "--input", approval)
    approved = invoke(
        workflow_cli,
        "report",
        "--input",
        assessment,
        "--output",
        directory / "approved-assessment.json",
    )
    repo.add_review(
        finding_id,
        ReviewRequest(
            action="MODIFY",
            reviewed_value=ReviewedValue(
                statement="At least five years of PostgreSQL experience are required.",
                category="MUST",
                requirement_type="TECHNICAL",
            ),
            comment="Simulation of changed interpretation; duration remains unverified",
        ),
    )
    stale = invoke(
        workflow_cli,
        "report",
        "--input",
        assessment,
        "--output",
        directory / "stale-assessment.json",
    )
    repeated = invoke(intake_cli, "commit", "--input", source, "--confirm-manual-intake")
    return dict(
        simulation_only=True,
        analysis_id=run.id,
        finding_id=finding_id,
        document_id=document.id,
        pdf_sha256=document.sha256,
        snapshot_hash=snapshot.content_hash,
        validation=validation["status"],
        commit=committed["status"],
        repeated_commit=repeated["status"],
        before_review=before["summary"],
        approved=approved["summary"],
        stale=stale["summary"],
    )


if __name__ == "__main__":
    parser = SafeParser(description=__doc__)
    parser.add_argument(
        "--directory", required=True, type=Path, help="Fresh private synthetic directory"
    )
    args = parser.parse_args()
    print(json.dumps(run_demo(args.directory), sort_keys=True))
