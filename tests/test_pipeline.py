"""Synthetic native pipeline persistence, isolation, strictness and concurrency."""

import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from uuid import UUID

import pytest
from opportunity_helpers import create_input as opportunity_input
from opportunity_helpers import update_input as opportunity_update
from pipeline_helpers import OPPORTUNITY_ID, PIPELINE_ID, create_input, update_body, update_input
from pydantic import ValidationError

from tendercite.domain.pipeline import PipelineCreate, PipelineError, PipelineStage, PipelineUpdate
from tendercite.repositories.sqlite import SqliteRepository


@pytest.fixture
def repo(tmp_path):
    repo = SqliteRepository(tmp_path / "synthetic.db")
    repo.create_opportunity(opportunity_input())
    return repo


def test_explicit_creation_restart_and_live_opportunity_metadata(repo):
    before = repo.get_opportunity(OPPORTUNITY_ID)
    assert repo.list_pipeline().entries == ()
    result = repo.create_pipeline_entry(create_input())
    record = result.entry.record
    assert result.status == "CREATED" and record.revision == 1
    assert record.values.stage == "SAVED" and record.values.priority == "MEDIUM"
    assert record.values.notes == record.values.next_action == ""
    assert record.values.next_action_due_date is None
    restarted = SqliteRepository(repo.database_path)
    assert restarted.get_pipeline_entry(PIPELINE_ID) == result.entry
    assert restarted.get_opportunity(OPPORTUNITY_ID) == before
    changed = before.opportunity.model_copy(update={"title": "Revised synthetic opportunity"})
    restarted.update_opportunity(OPPORTUNITY_ID, opportunity_update(changed))
    entry = restarted.get_pipeline_entry(PIPELINE_ID)
    assert entry.record == record and entry.opportunity.title == changed.title
    assert entry.opportunity.revision == 2
    with repo._connect() as conn:
        payload = conn.execute("SELECT payload FROM native_pipeline").fetchone()[0]
    assert "title" not in json.loads(payload) and "source_url" not in payload


def test_duplicate_opportunity_does_not_reset_existing_stage_or_notes(repo):
    repo.create_pipeline_entry(create_input())
    saved = repo.update_pipeline_entry(PIPELINE_ID, update_input()).entry
    before = repo.database_path.read_bytes()
    for identity in (PIPELINE_ID, UUID(int=1601)):
        result = repo.create_pipeline_entry(create_input(pipeline_id=str(identity)))
        assert result.status == "ALREADY_PRESENT" and result.entry == saved
    assert repo.database_path.read_bytes() == before
    assert len(repo.list_pipeline().entries) == 1


def test_unknown_opportunity_missing_entry_and_identity_conflicts(repo):
    with pytest.raises(PipelineError, match="OPPORTUNITY_NOT_FOUND"):
        repo.create_pipeline_entry(create_input(opportunity_id=str(UUID(int=999))))
    with pytest.raises(PipelineError, match="PIPELINE_NOT_FOUND"):
        repo.get_pipeline_entry(PIPELINE_ID)
    repo.create_pipeline_entry(create_input())
    repo.create_opportunity(opportunity_input(opportunity_id=str(UUID(int=1201))))
    with pytest.raises(PipelineError, match="PIPELINE_ID_CONFLICT"):
        repo.create_pipeline_entry(create_input(opportunity_id=str(UUID(int=1201))))
    assert len(repo.list_pipeline().entries) == 1


def test_all_stages_priorities_notes_and_dates_are_explicit_and_unrestricted(repo):
    original = repo.get_opportunity(OPPORTUNITY_ID)
    repo.create_pipeline_entry(create_input())
    for revision, stage in enumerate(reversed(list(PipelineStage)), 1):
        priority = ["LOW", "MEDIUM", "HIGH"][revision % 3]
        record = repo.update_pipeline_entry(
            PIPELINE_ID, update_input(revision, stage=stage.value, priority=priority)
        ).entry.record
        assert record.revision == revision + 1 and record.values.stage == stage
        assert record.values.priority == priority and record.values.notes == "Synthetic manual note"
        assert record.values.next_action == "Draft a synthetic proposal"
        assert record.values.next_action_due_date.isoformat() == "2027-02-15"
    assert repo.get_opportunity(OPPORTUNITY_ID) == original
    assert original.opportunity.deadline.isoformat() == "2027-03-01"
    assert repo.list_findings() == [] and repo.list_profiles().profiles == ()
    assert repo.list_capability_snapshots() == []
    before = repo.database_path.read_bytes()
    assert repo.list_pipeline().entries[0].record == record
    assert repo.get_pipeline_entry(PIPELINE_ID).record == record
    assert repo.database_path.read_bytes() == before


def test_clearing_optional_fields_and_read_only_repository(repo):
    repo.create_pipeline_entry(create_input())
    repo.update_pipeline_entry(PIPELINE_ID, update_input())
    record = repo.update_pipeline_entry(
        PIPELINE_ID, update_input(2, notes="", next_action="", next_action_due_date=None)
    ).entry.record
    assert (
        record.values.notes == record.values.next_action == ""
        and record.values.next_action_due_date is None
    )
    readonly = SqliteRepository(repo.database_path, read_only=True)
    assert readonly.get_pipeline_entry(PIPELINE_ID).record == record
    with pytest.raises(PipelineError, match="DATABASE_FAILED"):
        readonly.update_pipeline_entry(PIPELINE_ID, update_input(3))


def test_listing_filtering_scope_and_order(repo):
    for i, stage in enumerate(("SAVED", "PREPARING", "WON")):
        opportunity = UUID(int=1200 + i)
        if i:
            repo.create_opportunity(opportunity_input(opportunity_id=str(opportunity)))
        pipeline = UUID(int=1600 + i)
        repo.create_pipeline_entry(
            create_input(pipeline_id=str(pipeline), opportunity_id=str(opportunity))
        )
        repo.update_pipeline_entry(pipeline, update_input(stage=stage))
    first = repo.list_pipeline(limit=2)
    assert [e.record.pipeline_id for e in first.entries] == [UUID(int=1600), UUID(int=1601)]
    assert first.next_offset == 2
    assert repo.list_pipeline(limit=2, offset=2).next_offset is None
    assert repo.list_pipeline(stage=PipelineStage.WON).entries[0].record.pipeline_id == UUID(
        int=1602
    )
    assert repo.list_pipeline(stage=PipelineStage.LOST).entries == ()
    assert len(repo.list_pipeline(opportunity_id=OPPORTUNITY_ID).entries) == 1
    assert repo.list_pipeline(opportunity_id=OPPORTUNITY_ID, stage=PipelineStage.WON).entries == ()


def test_serialized_concurrent_creations_and_stale_updates(repo):
    def create(i):
        return (
            SqliteRepository(repo.database_path, initialize=False)
            .create_pipeline_entry(create_input(pipeline_id=str(UUID(int=1600 + i))))
            .status
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(create, range(2))) == ["ALREADY_PRESENT", "CREATED"]
    identity = repo.list_pipeline().entries[0].record.pipeline_id

    def update(_):
        try:
            return (
                SqliteRepository(repo.database_path, initialize=False)
                .update_pipeline_entry(identity, update_input())
                .status
            )
        except PipelineError as exc:
            return exc.code.value

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(update, range(2))) == ["PIPELINE_REVISION_CONFLICT", "UPDATED"]
    assert repo.get_pipeline_entry(identity).record.revision == 2


@pytest.mark.parametrize(
    "change",
    [
        {"schema_version": "unsupported"},
        {"pipeline_id": "bad"},
        {"opportunity_id": None},
        {"values": {}},
        {"database_path": "/private"},
    ],
)
def test_strict_create_contract(change):
    from pipeline_helpers import create_body

    with pytest.raises(ValidationError):
        PipelineCreate.model_validate_json(json.dumps({**create_body(), **change}))


@pytest.mark.parametrize(
    "change",
    [
        {"stage": "AUTO"},
        {"priority": "URGENT"},
        {"notes": "x" * 4001},
        {"next_action": "x" * 1001},
        {"next_action_due_date": "2027-02-30"},
        {"next_action_due_date": "2027-02-15T12:00:00Z"},
        {"extra": "PRIVATE"},
    ],
)
def test_strict_update_contract(change):
    with pytest.raises(ValidationError):
        PipelineUpdate.model_validate_json(json.dumps(update_body(**change)))


def test_database_uniqueness_fk_and_corruption_do_not_overwrite(repo):
    repo.create_pipeline_entry(create_input())
    with repo._connect() as conn:
        row = conn.execute("SELECT * FROM native_pipeline").fetchone()
        for opportunity in (str(OPPORTUNITY_ID), str(UUID(int=999))):
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO native_pipeline VALUES (?, ?, ?, ?, ?)",
                    (str(UUID(int=1999)), opportunity, "SAVED", 1, row["payload"]),
                )
        conn.execute("UPDATE native_pipeline SET payload=?", ('{"PRIVATE": "PRIVATE"}',))
    before = repo.database_path.read_bytes()
    for operation in (
        lambda: repo.get_pipeline_entry(PIPELINE_ID),
        lambda: repo.list_pipeline(),
        lambda: repo.update_pipeline_entry(PIPELINE_ID, update_input()),
    ):
        with pytest.raises(PipelineError, match="PIPELINE_STORAGE_INTEGRITY"):
            operation()
    assert repo.database_path.read_bytes() == before
