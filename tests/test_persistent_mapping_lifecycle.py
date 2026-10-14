import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from threading import Barrier, Event

import pytest
from matching_helpers import SKILL_A, SKILL_B
from persistent_mapping_helpers import (
    approval_input,
    approved_mapping,
    draft_input,
    revision_input,
    revocation_input,
    seeded_repository,
)

from tendercite.domain.capability_mapping_records import (
    CapabilityMappingError,
    MappingErrorCode,
    MappingStatus,
)
from tendercite.domain.review import ReviewRequest
from tendercite.repositories.sqlite import SqliteRepository


@pytest.fixture
def repo(tmp_path):
    return seeded_repository(tmp_path / "synthetic.db")


def test_draft_approval_revision_and_revocation_keep_all_history(repo):
    draft = repo.create_capability_mapping_draft(draft_input(repo))
    assert draft.status == "DRAFT" and draft.revision == 1 and draft.approved_at is None
    approved = repo.approve_capability_mapping(approval_input(draft))
    assert approved.status == "APPROVED" and approved.revision == 2
    assert approved.approved_at == approved.recorded_at
    revised = repo.revise_capability_mapping(
        revision_input(repo, approved, skill_ids=(SKILL_B, SKILL_A))
    )
    assert revised.status == "DRAFT" and revised.skill_ids == (SKILL_A, SKILL_B)
    assert repo.get_capability_mapping(draft.mapping_id).status == MappingStatus.DRAFT
    approved_again = repo.approve_capability_mapping(approval_input(revised))
    revoked = repo.revoke_capability_mapping(revocation_input(approved_again))
    assert revoked.status == "REVOKED" and revoked.revision == 5
    assert repo.get_capability_mapping(draft.mapping_id).status == MappingStatus.REVOKED
    history = repo.capability_mapping_history(draft.mapping_id)
    assert history == [draft, approved, revised, approved_again, revoked]
    assert all(r.created_at == draft.created_at for r in history)
    restarted = SqliteRepository(repo.database_path)
    assert restarted.capability_mapping_history(draft.mapping_id) == history
    assert restarted.get_capability_mapping(draft.mapping_id).record == revoked


def test_duplicate_identity_and_conflicting_revisions_fail(repo):
    draft = repo.create_capability_mapping_draft(draft_input(repo))
    with pytest.raises(CapabilityMappingError, match="MAPPING_ALREADY_EXISTS"):
        repo.create_capability_mapping_draft(draft_input(repo))
    approved = repo.approve_capability_mapping(approval_input(draft))
    for operation, request in [
        (repo.approve_capability_mapping, approval_input(draft)),
        (repo.revise_capability_mapping, revision_input(repo, draft)),
        (repo.revoke_capability_mapping, revocation_input(draft)),
    ]:
        with pytest.raises(CapabilityMappingError, match="MAPPING_REVISION_CONFLICT"):
            operation(request)
    with pytest.raises(CapabilityMappingError, match="INVALID_MAPPING_TRANSITION"):
        repo.approve_capability_mapping(approval_input(approved))
    revoked = repo.revoke_capability_mapping(revocation_input(approved))
    with pytest.raises(CapabilityMappingError, match="INVALID_MAPPING_TRANSITION"):
        repo.revoke_capability_mapping(revocation_input(revoked))
    # Restoring a revoked mapping is an explicit new draft and separate approval.
    revised = repo.revise_capability_mapping(revision_input(repo, revoked))
    assert revised.status == "DRAFT"
    assert repo.approve_capability_mapping(approval_input(revised)).revision == 5


def test_database_guards_identity_and_immutable_revision_order(repo):
    draft = repo.create_capability_mapping_draft(draft_input(repo))
    with repo._connect() as conn:
        for sql in (
            "UPDATE capability_mapping_revisions SET status='REVOKED'",
            "DELETE FROM capability_mapping_revisions",
            "UPDATE capability_mappings SET finding_id='other'",
            "DELETE FROM capability_mappings",
        ):
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute(sql)
        for revision in (1, 3):
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO capability_mapping_revisions VALUES (?, ?, 'DRAFT', NULL, ?)",
                    (str(draft.mapping_id), revision, draft.model_dump_json()),
                )
    assert repo.capability_mapping_history(draft.mapping_id) == [draft]


def test_concurrent_approval_and_update_have_exactly_one_winner(repo):
    draft = repo.create_capability_mapping_draft(draft_input(repo))
    barrier = Barrier(2)

    def worker(approve):
        connection = SqliteRepository(repo.database_path, read_only=False)
        operation = (
            connection.approve_capability_mapping
            if approve
            else connection.revise_capability_mapping
        )
        request = (
            approval_input(draft)
            if approve
            else revision_input(connection, draft, skill_ids=(SKILL_A,))
        )
        barrier.wait(timeout=10)
        try:
            return operation(request)
        except CapabilityMappingError as error:
            return error.code.value

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(worker, [True, False]))
    assert sum(r == "MAPPING_REVISION_CONFLICT" for r in results) == 1
    assert len(repo.capability_mapping_history(draft.mapping_id)) == 2


@pytest.mark.parametrize("action", ["MODIFY", "CONFIRM", "REJECT"])
def test_new_review_invalidates_approval_even_when_text_is_identical(repo, action):
    approved = approved_mapping(repo)
    before = repo.capability_mapping_history(approved.mapping_id)
    value = repo.get_finding("finding-1").effective_value if action == "MODIFY" else None
    repo.add_review("finding-1", ReviewRequest(action=action, reviewed_value=value))
    inspection = repo.get_capability_mapping(approved.mapping_id)
    assert inspection.status == MappingStatus.STALE
    assert "REQUIREMENT_REVIEW_EVENT_CHANGED" in inspection.reasons
    assert inspection.record.status == "APPROVED"
    assert repo.capability_mapping_history(approved.mapping_id) == before
    assert repo.stale_capability_mappings()[0] == inspection
    with pytest.raises(CapabilityMappingError, match="INVALID_MAPPING_TRANSITION"):
        repo.approve_capability_mapping(approval_input(approved))


def test_draft_cannot_be_approved_against_a_new_review_without_revision(repo):
    draft = repo.create_capability_mapping_draft(draft_input(repo))
    event = repo.add_review("finding-1", ReviewRequest(action="CONFIRM"))
    with pytest.raises(CapabilityMappingError, match="REQUIREMENT_REVIEW_CONFLICT"):
        repo.approve_capability_mapping(approval_input(draft))
    with pytest.raises(CapabilityMappingError, match="REQUIREMENT_REVIEW_CONFLICT"):
        repo.approve_capability_mapping(approval_input(draft, expected_review_event_id=event.id))
    revised = repo.revise_capability_mapping(revision_input(repo, draft))
    assert (
        repo.approve_capability_mapping(approval_input(revised)).review_binding.review_event_id
        == event.id
    )


def test_approval_serializes_review_validation_and_persistence(repo, monkeypatch):
    draft = repo.create_capability_mapping_draft(draft_input(repo))
    reviewer = SqliteRepository(repo.database_path)
    approval_locked, review_attempted = Event(), Event()
    original_sources, original_connect = repo._mapping_sources, reviewer._connect

    def sources(*args, **kwargs):
        approval_locked.set()
        assert review_attempted.wait(timeout=5)
        return original_sources(*args, **kwargs)

    @contextmanager
    def reviewer_connection():
        with original_connect() as conn:
            conn.set_trace_callback(
                lambda sql: review_attempted.set() if sql == "BEGIN IMMEDIATE" else None
            )
            yield conn

    monkeypatch.setattr(repo, "_mapping_sources", sources)
    monkeypatch.setattr(reviewer, "_connect", reviewer_connection)
    with ThreadPoolExecutor(max_workers=2) as pool:
        approval = pool.submit(repo.approve_capability_mapping, approval_input(draft))
        assert approval_locked.wait(timeout=5)
        review = pool.submit(reviewer.add_review, "finding-1", ReviewRequest(action="CONFIRM"))
        approved = approval.result(timeout=10)
        event = review.result(timeout=10)
    monkeypatch.setattr(repo, "_mapping_sources", original_sources)
    assert approved.review_binding.review_event_id != event.id
    assert repo.get_capability_mapping(draft.mapping_id).status == MappingStatus.STALE


def test_identity_and_first_revision_roll_back_together(repo, monkeypatch):
    def fail(*_args):
        raise CapabilityMappingError(MappingErrorCode.DATABASE_FAILED)

    monkeypatch.setattr(repo, "_insert_mapping_revision", fail)
    with pytest.raises(CapabilityMappingError, match="DATABASE_FAILED"):
        repo.create_capability_mapping_draft(draft_input(repo))
    with repo._connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM capability_mappings").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM capability_mapping_revisions").fetchone()[0] == 0
