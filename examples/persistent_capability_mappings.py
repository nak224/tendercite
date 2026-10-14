"""Synthetic CLI pilot. Creates a fresh local demo; no real personnel or provider calls."""

import argparse
import hashlib
import json
from contextlib import redirect_stdout
from datetime import UTC, datetime
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import UUID

from tendercite.cli.capability_mappings import main as mapping_cli
from tendercite.domain.analysis import AnalysisRequest, AnalysisRun
from tendercite.domain.capability_mapping_records import (
    MappingApprovalInput,
    MappingDraftInput,
    MappingRevisionInput,
    MappingRevocationInput,
)
from tendercite.domain.capability_matching import CapabilityMatchingRequest
from tendercite.domain.models import EvidenceRef, Finding, PageRead, ReviewedValue
from tendercite.domain.review import ReviewRequest
from tendercite.repositories.sqlite import SqliteRepository
from tendercite.services.capabilities import read_capability_snapshot


def seed_demo(directory: Path):
    """Create disposable self-authored analysis and pinned snapshot, with simulated review."""
    database = directory / "synthetic.db"
    if database.exists():
        raise ValueError("Use a fresh demo directory")
    directory.mkdir(parents=True, exist_ok=True)
    repo = SqliteRepository(database)
    fixture = (
        Path(__file__).resolve().parents[1]
        / "tests/fixtures/capabilities/tendercite-capability-v1.json"
    )
    snapshot = read_capability_snapshot(fixture)
    repo.import_capability_snapshot(snapshot)
    quote = "The contractor must provide PostgreSQL development experience."
    now = datetime(2027, 2, 1, tzinfo=UTC)
    finding = Finding(
        id="synthetic-finding",
        analysis_run_id="synthetic-analysis",
        statement=quote,
        category="MUST",
        requirement_type="TECHNICAL",
        created_at=now,
        # Self-authored fixture represents source-grounded input, not a real tender or review.
        evidence=[
            EvidenceRef(
                document_id="synthetic-document",
                chunk_id="synthetic-chunk",
                page_number=1,
                quote=quote,
                char_start=0,
                char_end=len(quote),
                text_sha256=hashlib.sha256(quote.encode()).hexdigest(),
                grounding_status="VERIFIED_QUOTE",
            )
        ],
    )
    repo.save_document(
        document_id="synthetic-document",
        filename="self-authored.pdf",
        media_type="application/pdf",
        sha256="b" * 64,
        pages=[PageRead(document_id="synthetic-document", page_number=1, text=quote)],
        chunks=[],
    )
    repo.save_analysis(
        AnalysisRun(
            id=finding.analysis_run_id,
            provider="synthetic",
            model="unused",
            embedding_model="unused",
            prompt_version="synthetic",
            schema_version="synthetic",
            request=AnalysisRequest(document_ids=["synthetic-document"]),
            retrieved_chunk_ids=["synthetic-chunk"],
            created_at=now,
            findings=[finding],
        )
    )
    # Simulation of explicit local review; no genuine human or legal verification is claimed.
    repo.add_review(finding.id, ReviewRequest(action="CONFIRM", comment="Synthetic review example"))
    return repo, snapshot, finding


def run_demo(directory: Path):
    repo, snapshot, finding = seed_demo(directory)
    database = repo.database_path
    identity = dict(
        analysis_id=finding.analysis_run_id,
        finding_id=finding.id,
        snapshot_hash=snapshot.content_hash,
    )
    binding = repo.get_mapping_review_binding(**identity)
    skill_ids = (UUID("00000000-0000-0000-0000-000000000065"),)

    def write(name, model):
        path = directory / name
        # Demo output is under a fresh synthetic directory, never third-party records.
        with path.open("x", encoding="utf-8") as stream:
            stream.write(model.model_dump_json(indent=2))
        return path

    def invoke(command, path):
        capture = StringIO()
        with redirect_stdout(capture):
            status = mapping_cli(["--database", str(database), command, "--input", str(path)])
        if status:
            raise RuntimeError("Synthetic mapping CLI failed")
        return json.loads(capture.getvalue())

    draft_path = write(
        "0-draft.json",
        MappingDraftInput(
            **identity,
            operator="ALL_OF",
            skill_ids=skill_ids,
            expected_review_event_id=binding.review_event_id,
            expected_review_digest=binding.digest,
            rationale="Operator explicitly associates PostgreSQL UUID with this requirement",
            operator_alias="demo_operator",
        ),
    )
    draft_output = invoke("create", draft_path)
    record = repo.get_capability_mapping(draft_output["mapping"]["mapping_id"]).record
    selection_path = write(
        "2-match.json",
        CapabilityMatchingRequest(
            analysis_id=identity["analysis_id"],
            snapshot_hash=snapshot.content_hash,
            finding_ids=(finding.id,),
            mappings=(),
        ),
    )
    draft_match = invoke("match", selection_path)
    approval_path = write(
        "1-approval.json",
        MappingApprovalInput(
            mapping_id=record.mapping_id,
            expected_revision=1,
            expected_review_event_id=binding.review_event_id,
            expected_review_digest=binding.digest,
            rationale="Explicit operator approval of UUID association",
            operator_alias="demo_operator",
        ),
    )
    invoke("approve", approval_path)
    approved_match = invoke("match", selection_path)
    repo.add_review(
        finding.id,
        ReviewRequest(
            action="MODIFY",
            reviewed_value=ReviewedValue(
                statement="At least five years of PostgreSQL development experience are required.",
                category="MUST",
                requirement_type="TECHNICAL",
            ),
            comment="Synthetic changed interpretation; experience duration remains unverified",
        ),
    )
    stale_match = invoke("match", selection_path)
    current = repo.get_mapping_review_binding(**identity)
    write(
        "3-revision.json",
        MappingRevisionInput(
            mapping_id=record.mapping_id,
            expected_revision=2,
            operator="ALL_OF",
            skill_ids=skill_ids,
            expected_review_event_id=current.review_event_id,
            expected_review_digest=current.digest,
            rationale="Explicit reconsideration after changed review; duration is not verified",
            operator_alias="demo_operator",
        ),
    )
    write(
        "4-reapproval.json",
        MappingApprovalInput(
            mapping_id=record.mapping_id,
            expected_revision=3,
            expected_review_event_id=current.review_event_id,
            expected_review_digest=current.digest,
            rationale="Approve the revised UUID association only",
            operator_alias="demo_operator",
        ),
    )
    write(
        "5-revocation.json",
        MappingRevocationInput(
            mapping_id=record.mapping_id,
            expected_revision=4,
            rationale="Explicit withdrawal of this association",
            operator_alias="demo_operator",
        ),
    )
    return dict(
        mapping_id=str(record.mapping_id),
        snapshot_hash=snapshot.content_hash,
        before_approval=draft_match,
        after_approval=approved_match,
        after_changed_review=stale_match,
        history_statuses=[
            r.status.value for r in repo.capability_mapping_history(record.mapping_id)
        ],
        current_status=repo.get_capability_mapping(record.mapping_id).status.value,
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--directory", type=Path, help="Fresh local directory for synthetic artifacts"
    )
    args = parser.parse_args()
    if args.directory:
        result = run_demo(args.directory)
    else:
        with TemporaryDirectory(prefix="tendercite-mapping-demo-") as temporary:
            result = run_demo(Path(temporary))
    print(json.dumps(result, sort_keys=True))
