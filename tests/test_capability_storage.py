import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import timedelta

import pytest
from capability_helpers import CONTENT_HASH, FIXTURE, capacity, encode, example
from test_analysis import create_run

from tendercite.api.dependencies import get_repository
from tendercite.domain.capabilities_v1 import canonical_json
from tendercite.main import app
from tendercite.repositories.sqlite import SqliteRepository
from tendercite.services.capabilities import CapabilityError, validate_capability_json


def snapshot():
    return validate_capability_json(FIXTURE.read_bytes())


def test_import_idempotency_original_timestamp_and_restart_lookup(tmp_path):
    path = tmp_path / "test.db"
    repo = SqliteRepository(path)
    value = snapshot()
    first = repo.import_capability_snapshot(value)
    repeated = repo.import_capability_snapshot(value)
    assert first.inserted and not repeated.inserted
    assert first.metadata == repeated.metadata
    assert first.metadata.imported_at.utcoffset() == timedelta(0)
    restored = SqliteRepository(path, read_only=True)
    assert restored.get_capability_snapshot(CONTENT_HASH) == value
    assert restored.list_capability_snapshots() == [first.metadata]
    with sqlite3.connect(path) as conn:
        stored = conn.execute("SELECT canonical_json FROM capability_snapshots").fetchone()[0]
    assert stored.encode() == canonical_json(value)


def test_concurrent_imports_have_one_database_enforced_identity(tmp_path):
    path = tmp_path / "concurrent.db"
    SqliteRepository(path)
    value = snapshot()

    def insert(_index):
        return SqliteRepository(path).import_capability_snapshot(value)

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(insert, range(16)))
    assert sum(r.inserted for r in results) == 1
    assert len({r.metadata.imported_at for r in results}) == 1
    assert len(SqliteRepository(path).list_capability_snapshots()) == 1
    with sqlite3.connect(path) as conn, pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO capability_snapshots SELECT * FROM capability_snapshots")


def test_historical_states_share_period_without_becoming_authoritative(tmp_path):
    repo = SqliteRepository(tmp_path / "history.db")
    first = snapshot()
    body = example()
    capacity(body)["remaining_hours"] = "-2.50"
    second = validate_capability_json(encode(body))
    repo.import_capability_snapshot(first)
    repo.import_capability_snapshot(second)
    assert first.content_hash != second.content_hash
    assert first.assessment_period == second.assessment_period
    assert repo.get_capability_snapshot(first.content_hash) == first
    assert repo.get_capability_snapshot(second.content_hash) == second
    assert len(repo.list_capability_snapshots()) == 2
    assert repo.list_matrix() == []


def test_bounded_listing_is_deterministic_and_reads_only_metadata(tmp_path, monkeypatch):
    path = tmp_path / "listing.db"
    repo = SqliteRepository(path)
    values = []
    for i in range(3):
        body = example()
        capacity(body)["remaining_hours"] = f"{i}.00"
        value = validate_capability_json(encode(body))
        repo.import_capability_snapshot(value)
        values.append(value)
    with sqlite3.connect(path) as conn:
        conn.execute("UPDATE capability_snapshots SET imported_at='2027-02-01T00:00:00+00:00'")
    # Metadata listing must not parse full personnel JSON or fetch a latest-authoritative object.
    monkeypatch.setattr(repo, "_stored_capability", lambda _: pytest.fail("Loaded full snapshot"))
    rows = repo.list_capability_snapshots(limit=2)
    assert [r.content_hash for r in rows] == sorted(v.content_hash for v in values)[:2]
    assert rows == repo.list_capability_snapshots(limit=2)
    assert not any(hasattr(row, "workforce_members") for row in rows)


@pytest.mark.parametrize("limit", [0, -1, 101, True, 1.5, "5"])
def test_list_limit_is_strictly_bounded(tmp_path, limit):
    with pytest.raises(CapabilityError) as error:
        SqliteRepository(tmp_path / "test.db").list_capability_snapshots(limit=limit)
    assert str(error.value) == "INVALID_ARGUMENTS"


def test_explicit_missing_lookup_and_read_only_database(tmp_path):
    path = tmp_path / "test.db"
    repo = SqliteRepository(path)
    read_only = SqliteRepository(path, read_only=True)
    assert read_only.get_capability_snapshot("sha256:" + "0" * 64) is None
    with pytest.raises(CapabilityError):
        read_only.import_capability_snapshot(snapshot())
    assert repo.list_capability_snapshots() == []
    with pytest.raises(sqlite3.OperationalError):
        SqliteRepository(tmp_path / "missing.db", read_only=True).list_capability_snapshots()
    assert not (tmp_path / "missing.db").exists()


@pytest.mark.parametrize("change", ["payload", "identity", "period", "timestamp", "noncanonical"])
def test_corrupted_stored_content_fails_lookup_and_idempotency_without_overwrite(tmp_path, change):
    path = tmp_path / "tampered.db"
    repo = SqliteRepository(path)
    value = snapshot()
    repo.import_capability_snapshot(value)
    with sqlite3.connect(path) as conn:
        if change in ("payload", "noncanonical"):
            text = canonical_json(value).decode()
            text = text.replace("Python", "Altered") if change == "payload" else " " + text
            conn.execute("UPDATE capability_snapshots SET canonical_json=?", (text,))
        elif change == "identity":
            conn.execute("UPDATE capability_snapshots SET content_hash=?", ("sha256:" + "0" * 64,))
        elif change == "period":
            conn.execute("UPDATE capability_snapshots SET assessment_period_start='2027-02-02'")
        else:
            conn.execute("UPDATE capability_snapshots SET imported_at='invalid-time'")
        before = conn.execute("SELECT * FROM capability_snapshots").fetchall()
    lookup = "sha256:" + "0" * 64 if change == "identity" else CONTENT_HASH
    with pytest.raises(CapabilityError) as error:
        repo.get_capability_snapshot(lookup)
    assert str(error.value) == "STORAGE_INTEGRITY"
    if change != "identity":
        with pytest.raises(CapabilityError) as error:
            repo.import_capability_snapshot(value)
        assert str(error.value) == "STORAGE_INTEGRITY"
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT * FROM capability_snapshots").fetchall() == before


def test_invalid_model_cannot_bypass_validation_and_insert_partial_state(tmp_path):
    repo = SqliteRepository(tmp_path / "invalid.db")
    value = snapshot().model_copy(update={"content_hash": "sha256:" + "0" * 64})
    with pytest.raises(CapabilityError) as error:
        repo.import_capability_snapshot(value)
    assert str(error.value) == "HASH_MISMATCH"
    assert repo.list_capability_snapshots() == []


def test_initialization_and_import_preserve_all_existing_tender_data(client, retrieval, tmp_path):
    run = create_run(client).json()
    finding = run["findings"][0]
    fid = finding["id"]
    assert (
        client.post(f"/api/v1/findings/{fid}/reviews", json={"action": "CONFIRM"}).status_code
        == 201
    )
    assert (
        client.patch(
            f"/api/v1/go-no-go/{fid}",
            json={"status": "FULFILLED", "rationale": "Synthetic human decision"},
        ).status_code
        == 200
    )
    existing = app.dependency_overrides[get_repository]()
    tables = ("documents", "pages", "chunks", "analyses", "findings", "reviews", "assessments")

    def rows():
        with sqlite3.connect(existing.database_path) as conn:
            return {
                table: conn.execute(f"SELECT * FROM {table} ORDER BY rowid").fetchall()
                for table in tables
            }

    before, matrix = rows(), existing.list_matrix()
    with sqlite3.connect(existing.database_path) as conn:
        conn.execute("DROP TABLE capability_snapshots")  # Simulate an existing pre-integration DB.
    restarted = SqliteRepository(existing.database_path)
    assert rows() == before
    restarted.import_capability_snapshot(snapshot())
    assert rows() == before
    assert restarted.get_analysis(run["id"]) == existing.get_analysis(run["id"])
    assert restarted.list_reviews(fid) == existing.list_reviews(fid)
    assert restarted.list_matrix() == matrix
    assert client.get("/api/v1/go-no-go").json()[0]["status"] == "FULFILLED"


def test_failure_after_insert_rolls_back_before_commit(tmp_path, monkeypatch):
    repo = SqliteRepository(tmp_path / "rollback.db")

    def failure(_row):
        raise RuntimeError("Synthetic failure")

    monkeypatch.setattr(repo, "_stored_capability", failure)
    with pytest.raises(RuntimeError):
        repo.import_capability_snapshot(snapshot())
    assert repo.list_capability_snapshots() == []


def test_metadata_listing_cannot_read_personnel_payload_column(tmp_path, monkeypatch):
    repo = SqliteRepository(tmp_path / "column-privacy.db")
    repo.import_capability_snapshot(snapshot())
    connect = repo._connect

    @contextmanager
    def guarded_connection():
        with connect() as connection:

            def authorize(action, table, column, _database, _trigger):
                if (
                    action == sqlite3.SQLITE_READ
                    and table == "capability_snapshots"
                    and column == "canonical_json"
                ):
                    return sqlite3.SQLITE_DENY
                return sqlite3.SQLITE_OK

            connection.set_authorizer(authorize)
            yield connection

    monkeypatch.setattr(repo, "_connect", guarded_connection)
    assert repo.list_capability_snapshots(limit=1)[0].content_hash == CONTENT_HASH
    with pytest.raises(sqlite3.DatabaseError):
        repo.get_capability_snapshot(CONTENT_HASH)
