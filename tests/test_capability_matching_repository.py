import socket
import sqlite3
from contextlib import contextmanager

import httpx
import pytest
from capability_helpers import example
from matching_helpers import SKILL_A, SKILL_B, analysis, finding, request, snapshot

from tendercite.domain.capability_matching import CapabilityMatchingRequest, RecordedSkillCoverage
from tendercite.domain.models import AssessmentRequest, PageRead, ReviewStatus
from tendercite.domain.review import ReviewRequest
from tendercite.repositories.sqlite import SqliteRepository
from tendercite.services.capabilities import CapabilityError
from tendercite.services.capability_matching import (
    CapabilityMatchingError,
    match_capabilities,
    match_from_repository,
)
from tendercite.services.llm.openai_compatible import OpenAICompatibleLLM
from tendercite.services.retrieval.embeddings import SentenceTransformerEmbeddingProvider


@pytest.fixture
def matching_repository(tmp_path):
    repo = SqliteRepository(tmp_path / "synthetic-matching.db")
    value = finding(review_status=ReviewStatus.UNREVIEWED)
    repo.save_document(
        document_id="synthetic-document",
        filename="synthetic.pdf",
        media_type="application/pdf",
        sha256="b" * 64,
        pages=[PageRead(document_id="synthetic-document", page_number=1, text=value.statement)],
        chunks=[],
    )
    repo.save_analysis(analysis(value))
    repo.import_capability_snapshot(snapshot())
    return repo


def test_adapter_applies_latest_review_without_using_unreviewed_analysis_copy(matching_repository):
    repo = matching_repository
    req = request(snapshot())
    assert (
        match_from_repository(req, repo).findings[0].coverage == RecordedSkillCoverage.NOT_EVALUATED
    )
    repo.add_review("finding-1", ReviewRequest(action="CONFIRM"))
    result = match_from_repository(req, repo)
    assert result.findings[0].review_status == ReviewStatus.CONFIRMED
    assert result.findings[0].coverage == RecordedSkillCoverage.ALL
    assert repo.get_analysis("analysis-1").findings[0].review_status == ReviewStatus.UNREVIEWED
    repo.add_review(
        "finding-1",
        ReviewRequest(
            action="MODIFY",
            reviewed_value={
                "statement": "Three years of PostgreSQL experience",
                "category": "MUST",
                "requirement_type": "TECHNICAL",
            },
        ),
    )
    result = match_from_repository(req, repo)
    assert result.findings[0].review_status == ReviewStatus.MODIFIED
    assert result.findings[0].reviewed_value.statement.startswith("Three years")
    assert result.findings[0].source_references[0].quote == "Experience with PostgreSQL"
    assert result.findings[0].qualification == "NOT_VERIFIED"
    repo.add_review("finding-1", ReviewRequest(action="REJECT"))
    assert (
        match_from_repository(req, repo).findings[0].coverage == RecordedSkillCoverage.NOT_EVALUATED
    )


@pytest.mark.parametrize(
    "missing,error",
    [
        ("analysis", "ANALYSIS_NOT_FOUND"),
        ("finding", "FINDING_NOT_FOUND"),
        ("snapshot", "SNAPSHOT_NOT_FOUND"),
    ],
)
def test_missing_inputs_never_fall_back_or_become_negative_skill_evidence(
    matching_repository, missing, error
):
    body = request(snapshot()).model_dump(mode="json")
    if missing == "analysis":
        body["analysis_id"] = "missing-analysis"
    elif missing == "finding":
        body["finding_ids"] = ["missing-finding"]
        body["mappings"][0]["finding_id"] = "missing-finding"
    else:
        body["snapshot_hash"] = "sha256:" + "0" * 64
    with pytest.raises(CapabilityMatchingError, match=error):
        match_from_repository(CapabilityMatchingRequest.model_validate(body), matching_repository)


def test_foreign_analysis_finding_is_rejected(matching_repository):
    repo = matching_repository
    other = finding(
        "foreign-finding", analysis_id="other-analysis", review_status=ReviewStatus.UNREVIEWED
    )
    repo.save_analysis(analysis(other, analysis_id="other-analysis"))
    repo.add_review(other.id, ReviewRequest(action="CONFIRM"))
    with pytest.raises(CapabilityMatchingError, match="FINDING_ANALYSIS_MISMATCH"):
        match_from_repository(request(snapshot(), finding_ids=(other.id,)), repo)


def test_exact_historical_hash_selection_reads_one_snapshot_without_combining(
    matching_repository, monkeypatch
):
    repo = matching_repository
    repo.add_review("finding-1", ReviewRequest(action="CONFIRM"))
    old = snapshot()
    body = example()
    body["workforce_members"][0]["skills"].pop()
    newer = snapshot(body)
    repo.import_capability_snapshot(newer)
    reads = []
    original = repo.get_capability_snapshot

    def read(content_hash):
        reads.append(content_hash)
        return original(content_hash)

    monkeypatch.setattr(repo, "get_capability_snapshot", read)
    monkeypatch.setattr(
        repo, "list_capability_snapshots", lambda **_kw: pytest.fail("Selected latest snapshot")
    )
    first = match_from_repository(request(old, skills=(SKILL_A, SKILL_B)), repo)
    assert reads == [old.content_hash]
    assert first.findings[0].coverage == RecordedSkillCoverage.ALL
    second = match_from_repository(request(newer, skills=(SKILL_A, SKILL_B)), repo)
    assert reads == [old.content_hash, newer.content_hash]
    assert second.findings[0].coverage == RecordedSkillCoverage.SOME
    assert first.snapshot_hash != second.snapshot_hash
    assert first.assessment_period == second.assessment_period
    with pytest.raises(CapabilityMatchingError, match="SNAPSHOT_HASH_MISMATCH"):
        match_capabilities(
            request(old),
            analysis=repo.get_analysis("analysis-1"),
            findings=[repo.get_finding("finding-1")],
            snapshot=newer,
        )


def test_matching_prohibits_writes_network_and_llm_preserving_all_decisions(
    matching_repository, monkeypatch
):
    repo = matching_repository
    repo.add_review("finding-1", ReviewRequest(action="CONFIRM"))
    repo.set_assessment(
        "finding-1", AssessmentRequest(status="FULFILLED", rationale="Synthetic human decision")
    )
    tables = (
        "documents",
        "pages",
        "chunks",
        "analyses",
        "findings",
        "reviews",
        "assessments",
        "capability_snapshots",
    )

    def contents():
        with sqlite3.connect(repo.database_path) as conn:
            return {
                table: conn.execute(f"SELECT * FROM {table} ORDER BY rowid").fetchall()
                for table in tables
            }

    before, database_bytes, matrix = contents(), repo.database_path.read_bytes(), repo.list_matrix()
    connect = repo._connect

    @contextmanager
    def readonly_connection():
        with connect() as connection:
            connection.execute("PRAGMA query_only=ON")
            yield connection

    def forbidden(*_a, **_kw):
        pytest.fail("Matching attempted a write, provider request, model load or network call")

    monkeypatch.setattr(repo, "_connect", readonly_connection)
    for method in (
        "_initialize",
        "add_review",
        "set_assessment",
        "save_analysis",
        "import_capability_snapshot",
    ):
        monkeypatch.setattr(repo, method, forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", forbidden)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", forbidden)
    monkeypatch.setattr(OpenAICompatibleLLM, "request_completion", forbidden)
    monkeypatch.setattr(SentenceTransformerEmbeddingProvider, "__init__", forbidden)
    result = match_from_repository(request(snapshot()), repo)
    assert result.findings[0].qualification == "NOT_VERIFIED"
    assert result.findings[0].coverage == RecordedSkillCoverage.ALL
    assert contents() == before and repo.database_path.read_bytes() == database_bytes
    assert repo.list_matrix() == matrix


def test_readonly_adapter_does_not_initialize_tables(matching_repository):
    repo = matching_repository
    repo.add_review("finding-1", ReviewRequest(action="CONFIRM"))
    readonly = SqliteRepository(repo.database_path, read_only=True)
    assert (
        match_from_repository(request(snapshot()), readonly).findings[0].coverage
        == RecordedSkillCoverage.ALL
    )


def test_corrupted_snapshot_is_rejected_before_candidate_coverage(matching_repository):
    repo = matching_repository
    with sqlite3.connect(repo.database_path) as conn:
        conn.execute(
            "UPDATE capability_snapshots SET "
            "canonical_json=replace(canonical_json,'Python','Changed')"
        )
    with pytest.raises(CapabilityError, match="STORAGE_INTEGRITY"):
        match_from_repository(request(snapshot()), repo)
