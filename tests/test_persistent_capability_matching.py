import socket
import sqlite3
from contextlib import contextmanager

import httpx
import pytest
from capability_helpers import example
from matching_helpers import SKILL_A, SKILL_B, finding, request, snapshot
from persistent_mapping_helpers import (
    approval_input,
    approved_mapping,
    draft_input,
    revision_input,
    revocation_input,
    seeded_repository,
)

from tendercite.domain.capability_mapping_records import CapabilityMappingError
from tendercite.domain.capability_matching import RecordedSkillCoverage
from tendercite.domain.models import AssessmentRequest, ReviewedValue, ReviewStatus
from tendercite.domain.review import ReviewRequest
from tendercite.repositories.sqlite import SqliteRepository
from tendercite.services.capability_matching import match_from_repository
from tendercite.services.llm.openai_compatible import OpenAICompatibleLLM
from tendercite.services.persistent_capability_matching import match_persisted_capabilities
from tendercite.services.retrieval.embeddings import SentenceTransformerEmbeddingProvider


@pytest.fixture
def repo(tmp_path):
    return seeded_repository(tmp_path / "synthetic.db")


def match(repo, snap=None, finding_ids=("finding-1",)):
    return match_persisted_capabilities(
        analysis_id="analysis-1",
        snapshot_hash=(snap or snapshot()).content_hash,
        finding_ids=finding_ids,
        repository=repo,
    )


def test_only_current_approval_is_used_and_result_reuses_original_contract(repo):
    assert match(repo).findings[0].non_evaluation_reasons == ("NO_EXPLICIT_SKILL_MAPPING",)
    draft = repo.create_capability_mapping_draft(draft_input(repo))
    assert "PERSISTED_MAPPING_NOT_APPROVED" in match(repo).findings[0].non_evaluation_reasons
    approved = repo.approve_capability_mapping(approval_input(draft))
    result = match(repo)
    assert result == match_from_repository(request(snapshot()), repo)
    assert result.schema_version == "capability-matching-result-1"
    assert repo.usable_capability_mappings(request(snapshot(), mappings=())) == (approved,)
    revised = repo.revise_capability_mapping(
        revision_input(repo, approved, skill_ids=(SKILL_A, SKILL_B))
    )
    assert match(repo).findings[0].coverage == RecordedSkillCoverage.NOT_EVALUATED
    assert repo.usable_capability_mappings(request(snapshot(), mappings=())) == ()
    assert "PERSISTED_MAPPING_NOT_APPROVED" in match(repo).findings[0].non_evaluation_reasons
    assert len(repo.capability_mapping_history(approved.mapping_id)) == 3
    repo.revoke_capability_mapping(revocation_input(revised))
    result = match(repo)
    assert result.request.mappings == ()
    assert "PERSISTED_MAPPING_REVOKED" in result.findings[0].non_evaluation_reasons


def test_stale_binding_is_distinct_from_never_mapped_and_requires_explicit_reapproval(repo):
    approved = approved_mapping(repo)
    repo.add_review(
        "finding-1",
        ReviewRequest(
            action="MODIFY",
            reviewed_value=ReviewedValue(
                statement="At least five years of PostgreSQL experience",
                category="MUST",
                requirement_type="TECHNICAL",
            ),
        ),
    )
    result = match(repo)
    row = result.findings[0]
    assert row.mapping is None and row.coverage == RecordedSkillCoverage.NOT_EVALUATED
    assert "PERSISTED_MAPPING_STALE" in row.non_evaluation_reasons
    assert row.original_value.statement == "Experience with PostgreSQL"
    assert row.reviewed_value.statement.startswith("At least five")
    assert row.source_references[0].quote == row.original_value.statement
    assert "CHANGED_REVIEWED_MEANING_NOT_SOURCE_VERIFIED" in [
        limitation.code for limitation in row.limitations
    ]
    revised = repo.revise_capability_mapping(revision_input(repo, approved))
    assert match(repo).findings[0].coverage == RecordedSkillCoverage.NOT_EVALUATED
    repo.approve_capability_mapping(approval_input(revised))
    row = match(repo).findings[0]
    assert row.coverage == RecordedSkillCoverage.ALL
    assert row.qualification == "NOT_VERIFIED" and row.capacity_evaluation == "NOT_EVALUATED"


def test_exact_snapshot_identity_never_falls_back_or_combines_assignments(repo):
    first = approved_mapping(repo)
    body = example()
    body["workforce_members"][0]["skills"].pop()
    second = snapshot(body)
    repo.import_capability_snapshot(second)
    assert match(repo, second).findings[0].non_evaluation_reasons == ("NO_EXPLICIT_SKILL_MAPPING",)
    assert match(repo).findings[0].coverage == RecordedSkillCoverage.ALL
    draft = repo.create_capability_mapping_draft(
        draft_input(
            repo,
            snapshot_hash=second.content_hash,
            skill_ids=(SKILL_A, SKILL_B),
        )
    )
    second_approval = repo.approve_capability_mapping(approval_input(draft))
    assert match(repo, second).findings[0].coverage == RecordedSkillCoverage.SOME
    assert first.mapping_id != second_approval.mapping_id
    assert [
        m.record.mapping_id
        for m in repo.list_capability_mappings(snapshot_hash=second.content_hash)
    ] == [draft.mapping_id]
    assert len(repo.list_capability_mappings(analysis_id="analysis-1")) == 2
    assert repo.list_capability_mappings(analysis_id="other-analysis") == []
    assert match(repo, second).snapshot_hash == second.content_hash


def test_different_catalogs_remain_distinct(repo):
    approved_mapping(repo)
    body = example()
    body["workforce_members"][0]["skills"].pop()
    body["workforce_members"][1]["skills"][0]["skill_id"] = str(SKILL_A)
    body["skill_catalog"].pop()
    other = snapshot(body)
    repo.import_capability_snapshot(other)
    assert match(repo, other).request.mappings == ()
    with pytest.raises(CapabilityMappingError, match="UNKNOWN_CATALOG_SKILL_UUID"):
        repo.create_capability_mapping_draft(draft_input(repo, snapshot_hash=other.content_hash))


def test_matching_has_one_consistent_read_transaction_and_one_snapshot_load(tmp_path, monkeypatch):
    repo = seeded_repository(
        tmp_path / "multiple.db",
        findings=[
            finding("finding-2", review_status=ReviewStatus.UNREVIEWED),
            finding("finding-1", review_status=ReviewStatus.UNREVIEWED),
        ],
    )
    for fid in ("finding-2", "finding-1"):
        draft = repo.create_capability_mapping_draft(draft_input(repo, finding_id=fid))
        repo.approve_capability_mapping(approval_input(draft))
    trace, connections = [], []
    original = repo._connect

    @contextmanager
    def traced():
        with original() as conn:
            connections.append(conn)
            conn.set_trace_callback(trace.append)
            conn.execute("PRAGMA query_only=ON")
            yield conn

    monkeypatch.setattr(repo, "_connect", traced)
    result = match(repo, finding_ids=("finding-2", "finding-1"))
    assert [r.finding_id for r in result.findings] == ["finding-1", "finding-2"]
    assert len(connections) == 1 and "BEGIN" in trace
    assert sum("SELECT * FROM capability_snapshots" in sql for sql in trace) == 1
    assert result.request.mappings[0].finding_id == "finding-1"


def test_workflow_and_matching_preserve_original_records_and_decisions_without_network(
    repo, monkeypatch
):
    def forbidden(*_a, **_kw):
        pytest.fail("Mapping workflow attempted network, LLM or embeddings")

    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", forbidden)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", forbidden)
    monkeypatch.setattr(OpenAICompatibleLLM, "request_completion", forbidden)
    monkeypatch.setattr(SentenceTransformerEmbeddingProvider, "__init__", forbidden)
    repo.set_assessment(
        "finding-1", AssessmentRequest(status="FULFILLED", rationale="Human decision")
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

    before, matrix = contents(), repo.list_matrix()
    approved = approved_mapping(repo)
    result = match(repo)
    assert contents() == before and repo.list_matrix() == matrix
    assert result.qualification == "NOT_VERIFIED" and result.capacity_evaluation == "NOT_EVALUATED"
    db_bytes = repo.database_path.read_bytes()
    readonly = SqliteRepository(repo.database_path, read_only=True)
    assert match(readonly) == result and repo.database_path.read_bytes() == db_bytes
    with pytest.raises(CapabilityMappingError, match="DATABASE_FAILED"):
        readonly.revoke_capability_mapping(revocation_input(approved))
    repo.revoke_capability_mapping(revocation_input(approved))
    assert contents() == before and repo.list_matrix() == matrix


@pytest.mark.parametrize("limit", [0, 101, True, "1"])
def test_metadata_lists_are_bounded(repo, limit):
    with pytest.raises(CapabilityMappingError, match="INVALID_MAPPING_INPUT"):
        repo.list_capability_mappings(limit=limit)


def test_matching_selection_cannot_supply_inline_mappings(repo):
    with pytest.raises(CapabilityMappingError, match="INVALID_MAPPING_INPUT"):
        repo.load_persisted_matching_inputs(request(snapshot()))
    with pytest.raises(CapabilityMappingError, match="INVALID_MAPPING_INPUT"):
        repo.usable_capability_mappings(request(snapshot()))


def test_readonly_legacy_database_needs_no_migration_to_report_unmapped_findings(repo):
    # Simulate a database predating the two additive tables, with all old records retained.
    with repo._connect() as conn:
        conn.execute("DROP TABLE capability_mapping_revisions")
        conn.execute("DROP TABLE capability_mappings")
    before = repo.database_path.read_bytes()
    readonly = SqliteRepository(repo.database_path, read_only=True)
    assert readonly.list_capability_mappings() == []
    assert readonly.get_capability_mapping("unknown") is None
    assert readonly.capability_mapping_history("unknown") == []
    assert match(readonly).findings[0].non_evaluation_reasons == ("NO_EXPLICIT_SKILL_MAPPING",)
    assert repo.database_path.read_bytes() == before
    upgraded = SqliteRepository(repo.database_path)
    assert approved_mapping(upgraded).status == "APPROVED"


def test_partial_mapping_schema_is_an_integrity_error(repo):
    with repo._connect() as conn:
        conn.execute("DROP TABLE capability_mapping_revisions")
    with pytest.raises(CapabilityMappingError, match="MAPPING_STORAGE_INTEGRITY"):
        repo.list_capability_mappings()
