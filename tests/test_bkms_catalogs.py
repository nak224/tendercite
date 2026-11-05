"""Synthetic multi-day storage, lazy migration, search and unchanged native provenance."""

import hashlib
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime

import pytest
from bkms_helpers import NOTICE, archive, package

from tendercite.domain.bkms import BkmsError, ImportInput
from tendercite.domain.bkms_catalogs import MultiSearchInput, RemoveInput, notice_category
from tendercite.repositories.sqlite import SqliteRepository
from tendercite.services.bkms import parse_archive, search_catalogs


@pytest.fixture
def repo(tmp_path):
    return SqliteRepository(tmp_path / "synthetic.db")


def daily(day="2026-10-30", entries=None):
    entries = [(NOTICE, "tender", None)] if entries is None else entries
    records = []
    for identity, tag, published in entries:
        body = package(identity)
        body["publishedDate"] = published or day + "T08:00:00Z"
        body["releases"][0]["tag"] = [tag] if isinstance(tag, str) else tag
        records.append((identity + ".json", body))
    return parse_archive(
        archive(records), date.fromisoformat(day), retrieved_at=datetime(2026, 11, 4, tzinfo=UTC)
    )


def search(repo, **changes):
    _, coverage = repo.get_bkms_catalogs()
    return search_catalogs(
        repo, MultiSearchInput(collection_id=coverage.collection_id, keyword="Software", **changes)
    )


def legacy(repo, catalog):
    payload = catalog.model_dump_json()
    digest = hashlib.sha256(payload.encode()).hexdigest()
    with repo._connect() as conn:
        conn.execute(
            "CREATE TABLE bkms_catalog (singleton INTEGER PRIMARY KEY CHECK(singleton=1), "
            "content_hash TEXT NOT NULL, payload TEXT NOT NULL)"
        )
        conn.execute("INSERT INTO bkms_catalog VALUES (1,?,?)", (digest, payload))
    return digest, payload


def test_seven_days_repeat_requires_explicit_replace_and_remove(repo):
    summaries = [repo.save_bkms_catalog(daily(f"2026-10-{n}")) for n in range(24, 31)]
    before = repo.database_path.read_bytes()
    with pytest.raises(BkmsError, match="DAY_LIMIT_REACHED"):
        repo.save_bkms_catalog(daily("2026-10-31"))
    with pytest.raises(BkmsError, match="DAY_ALREADY_STORED"):
        repo.save_bkms_catalog(daily())
    assert repo.database_path.read_bytes() == before
    current = summaries[-1]
    replacement = daily(entries=[("10000002-1", "award", None)])
    updated = repo.save_bkms_catalog(replacement, expected_catalog_id=current.catalog_id)
    _, coverage = repo.get_bkms_catalogs()
    assert len(coverage.days) == 7 and coverage.days[0].catalog == updated
    assert coverage.days[-1].catalog == summaries[0]
    with pytest.raises(BkmsError, match="CATALOG_CONFLICT"):
        repo.remove_bkms_catalog(
            RemoveInput(export_day=current.export_day, expected_catalog_id=current.catalog_id)
        )
    removed = repo.remove_bkms_catalog(
        RemoveInput(export_day=updated.export_day, expected_catalog_id=updated.catalog_id)
    )
    assert len(removed.days) == 6
    repo.save_bkms_catalog(daily("2026-10-31"))
    assert len(repo.get_bkms_catalogs()[1].days) == 7


def test_legacy_read_migration_backup_and_removal_never_resurrects_data(repo):
    digest, payload = legacy(repo, daily())
    with repo._connect() as c:
        c.execute("DROP TABLE bkms_catalog_days")
    before = repo.database_path.read_bytes()
    readonly = SqliteRepository(repo.database_path, read_only=True)
    catalog, summary = readonly.get_bkms_catalog(digest)
    assert catalog == daily() and summary.catalog_id == digest
    assert readonly.get_bkms_catalogs()[1].days[0].catalog.catalog_id == digest
    assert repo.database_path.read_bytes() == before
    repo = SqliteRepository(repo.database_path)
    repo.save_bkms_catalog(daily("2026-10-31", [("10000002-1", "award", None)]))
    with repo._connect() as c:
        assert c.execute("SELECT content_hash,payload FROM bkms_catalog_legacy").fetchone()[:] == (
            digest,
            payload,
        )
        assert c.execute("SELECT count(*) FROM bkms_catalog_days").fetchone()[0] == 2
    request = ImportInput(catalog_id=digest, external_id=NOTICE)
    imported = repo.import_bkms_opportunity(request)
    repo.remove_bkms_catalog(RemoveInput(export_day=summary.export_day, expected_catalog_id=digest))
    restarted = SqliteRepository(repo.database_path)
    assert [d.catalog.export_day for d in restarted.get_bkms_catalogs()[1].days] == [
        date(2026, 10, 31)
    ]
    assert restarted.import_bkms_opportunity(request).record == imported.record
    with restarted._connect() as c:
        assert c.execute("SELECT payload FROM bkms_catalog_legacy").fetchone()[0] == payload


def test_migration_and_failed_replacement_roll_back_atomically(repo):
    digest, payload = legacy(repo, daily())
    with repo._connect() as c:
        c.execute(
            "CREATE TRIGGER fail_day BEFORE INSERT ON bkms_catalog_days "
            "WHEN NEW.export_day='2026-10-31' BEGIN SELECT RAISE(ABORT,'synthetic failure'); END"
        )
    from tendercite.domain.opportunities import OpportunityError

    with pytest.raises(OpportunityError, match="DATABASE_FAILED"):
        repo.save_bkms_catalog(daily("2026-10-31"))
    with repo._connect() as c:
        assert c.execute("SELECT payload FROM bkms_catalog").fetchone()[0] == payload
        assert c.execute("SELECT count(*) FROM bkms_catalog_days").fetchone()[0] == 0
        assert not c.execute(
            "SELECT name FROM sqlite_master WHERE name='bkms_catalog_legacy'"
        ).fetchone()
        c.execute("DROP TRIGGER fail_day")
    repo.save_bkms_catalog(daily("2026-10-31"))
    before = repo.get_bkms_catalog(digest)
    with repo._connect() as c:
        c.execute(
            "CREATE TRIGGER fail_replace BEFORE UPDATE ON bkms_catalog_days "
            "BEGIN SELECT RAISE(ABORT,'synthetic failure'); END"
        )
    with pytest.raises(OpportunityError):
        repo.save_bkms_catalog(
            daily(entries=[("10000002-1", "award", None)]), expected_catalog_id=digest
        )
    assert repo.get_bkms_catalog(digest) == before


def test_bad_legacy_data_is_preserved_and_does_not_block_native_records(repo):
    _, payload = legacy(repo, daily())
    with repo._connect() as c:
        c.execute("UPDATE bkms_catalog SET content_hash=?", ("f" * 64,))
    restarted = SqliteRepository(repo.database_path)
    from opportunity_helpers import create_input

    record = restarted.create_opportunity(create_input()).record
    assert restarted.get_opportunity(record.opportunity_id) == record
    with pytest.raises(BkmsError, match="STORAGE_INTEGRITY"):
        restarted.get_bkms_catalogs()
    with pytest.raises(BkmsError, match="STORAGE_INTEGRITY"):
        restarted.save_bkms_catalog(daily("2026-10-31"))
    with repo._connect() as c:
        assert c.execute("SELECT payload FROM bkms_catalog").fetchone()[0] == payload


def test_cross_day_dedup_keeps_observation_hashes_and_distinct_versions(repo):
    repo.save_bkms_catalog(daily("2026-10-29"))
    repo.save_bkms_catalog(
        daily(
            "2026-10-30",
            [
                (NOTICE, "tender", None),
                ("10000001-2", "tenderUpdate", None),
                ("10000002-1", "award", None),
            ],
        )
    )
    latest = search(repo)
    assert [h.source.external_id for h in latest.items] == ["10000001-2", "10000002-1"]
    assert latest.items[0].version_count == 2 and latest.items[0].is_latest_stored
    all_versions = search(repo, include_earlier_versions=True)
    old = next(h for h in all_versions.items if h.source.external_id == NOTICE)
    assert len(old.occurrences) == 2 and old.source.export_day == date(2026, 10, 30)
    assert old.source_record_hashes_differ and not old.is_latest_stored
    assert [o.export_day for o in old.occurrences] == [date(2026, 10, 30), date(2026, 10, 29)]
    assert (
        repo.get_bkms_catalog(old.occurrences[1].catalog_id)[0].records[0].record_sha256
        == old.occurrences[1].record_sha256
    )


def test_filter_categories_unknown_and_padded_version_ties(repo):
    rows = [
        (f"1000000{i}-1", tag, None)
        for i, tag in enumerate(
            [
                "tenderCancellation",
                "awardUpdate",
                "planningUpdate",
                [],
                ["tender", "future"],
                ["tender", "award"],
                "contract",
            ],
            1,
        )
    ]
    rows.append(("10000001-01", "tender", None))
    repo.save_bkms_catalog(daily(entries=rows))
    assert search(repo, notice_category="TENDER").total_count == 2
    assert search(repo, notice_category="AWARD").total_count == 1
    assert search(repo, notice_category="PLANNING").total_count == 1
    assert search(repo, notice_category="OTHER_UNKNOWN").total_count == 4
    assert all(h.is_latest_stored for h in search(repo, notice_category="TENDER").items)
    assert notice_category(("Tender",)) == "OTHER_UNKNOWN"


def test_latest_numeric_version_precedes_keyword_type_filters_and_missing_dates_sort_last(repo):
    older = package("10000001-2")
    newer = package("10000001-10")
    newer["releases"][0]["tag"] = ["award"]
    newer["releases"][0]["tender"]["title"] = "Fictional award"
    newer["releases"][0]["tender"]["description"] = "Separate retained wording."
    missing_date = package("10000002-1")
    missing_date.pop("publishedDate")
    repo.save_bkms_catalog(
        parse_archive(
            archive(
                [
                    ("10000001-2.json", older),
                    ("10000001-10.json", newer),
                    ("10000002-1.json", missing_date),
                ]
            ),
            date(2026, 10, 30),
        )
    )
    assert [h.source.external_id for h in search(repo).items] == ["10000002-1"]
    all_versions = search(repo, include_earlier_versions=True)
    assert [h.source.external_id for h in all_versions.items] == ["10000001-2", "10000002-1"]
    assert not all_versions.items[0].is_latest_stored
    assert search(repo, notice_category="TENDER").total_count == 1
    _, coverage = repo.get_bkms_catalogs()
    assert (
        search_catalogs(
            repo, MultiSearchInput(collection_id=coverage.collection_id, keyword="award")
        )
        .items[0]
        .source.external_id
        == "10000001-10"
    )


def test_newest_source_publication_first_ties_stable_and_scope_change_conflicts(repo):
    repo.save_bkms_catalog(daily("2026-10-29", [("10000003-1", "tender", "2026-10-30T10:00:00Z")]))
    repo.save_bkms_catalog(
        daily(
            entries=[
                ("10000002-1", "tender", "2026-10-30T11:00:00+01:00"),
                (NOTICE, "tender", "2026-10-30T12:00:00Z"),
            ]
        )
    )
    first = search(repo, limit=1)
    assert first.items[0].source.external_id == NOTICE and first.next_page == 2
    second = search_catalogs(
        repo,
        MultiSearchInput(
            collection_id=first.coverage.collection_id, keyword="Software", limit=1, page=2
        ),
    )
    assert second.items[0].source.external_id == "10000002-1"
    assert search(repo, limit=1, page=3).items[0].source.external_id == "10000003-1"
    repo.save_bkms_catalog(daily("2026-10-31"))
    with pytest.raises(BkmsError, match="CATALOG_CONFLICT"):
        search_catalogs(
            repo, MultiSearchInput(collection_id=first.coverage.collection_id, keyword="Software")
        )


def test_capacity_and_concurrent_replace_conflicts_leave_stored_data(repo, monkeypatch):
    from tendercite.repositories import bkms

    summary = repo.save_bkms_catalog(daily())
    before = repo.database_path.read_bytes()
    monkeypatch.setattr(bkms, "MAX_TOTAL_BYTES", repo.get_bkms_catalogs()[1].stored_bytes)
    with pytest.raises(BkmsError, match="TOTAL_STORAGE_LIMIT"):
        repo.save_bkms_catalog(daily("2026-10-31"))
    assert repo.database_path.read_bytes() == before
    monkeypatch.undo()

    def replace(n):
        try:
            r = SqliteRepository(repo.database_path, initialize=False)
            return r.save_bkms_catalog(
                daily(entries=[(f"1000000{n}-1", "tender", None)]),
                expected_catalog_id=summary.catalog_id,
            ).catalog_id
        except BkmsError as error:
            return error.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(replace, (2, 3)))
    assert results.count("BKMS_CATALOG_CONFLICT") == 1


def test_empty_local_collection_and_import_from_an_earlier_day(repo):
    assert search(repo).items == ()
    old = repo.save_bkms_catalog(daily())
    repo.save_bkms_catalog(daily("2026-10-31", [("10000002-1", "award", None)]))
    saved = repo.import_bkms_opportunity(ImportInput(catalog_id=old.catalog_id, external_id=NOTICE))
    assert saved.record.opportunity.external_source.export_day == old.export_day
    assert (
        saved.record.opportunity.required_skills == ()
        and saved.record.opportunity.verification_status == "NOT_VERIFIED"
    )
