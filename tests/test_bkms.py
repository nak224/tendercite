"""Synthetic bounded exports, deterministic local search and native persistence."""

import hashlib
from concurrent.futures import ThreadPoolExecutor
from datetime import date

import httpx
import pytest
from bkms_helpers import DAY, NOTICE, archive, package, provider
from opportunity_helpers import create_input as manual_input
from opportunity_helpers import update_input
from pydantic import ValidationError

from tendercite.domain.bkms import BkmsError, ImportInput, LoadInput, SearchInput
from tendercite.domain.bkms_reference import opportunity_id, validate_record_url
from tendercite.domain.opportunities import OpportunityError
from tendercite.repositories.sqlite import SqliteRepository
from tendercite.services.bkms import parse_archive, search_catalog


@pytest.fixture
def repo(tmp_path):
    return SqliteRepository(tmp_path / "synthetic.db")


def loaded(repo, payload=None):
    return repo.save_bkms_catalog(
        provider(payload).load(LoadInput(export_day=date.fromisoformat(DAY)))
    )


def test_official_wire_bounded_local_search_and_source_identity(repo):
    payload = archive()
    summary = loaded(repo, payload)
    assert summary.record_count == summary.importable_count == 1
    assert summary.archive_sha256 == hashlib.sha256(payload).hexdigest()
    before = repo.database_path.read_bytes()
    results = search_catalog(repo, SearchInput(catalog_id=summary.catalog_id, keyword="PYTHON"))
    source = results.items[0]
    assert (
        source.external_id == NOTICE
        and source.original_metadata.title == "Synthetic software procurement"
    )
    assert source.original_metadata.deadline == "2026-12-04T12:00:00+01:00"
    assert source.original_url.endswith("/ui/notices/10000001?noticeVersion=1")
    assert (
        "contactPoint" not in source.model_dump_json()
        and "do-not-retain" not in source.model_dump_json()
    )
    assert repo.database_path.read_bytes() == before
    assert (
        search_catalog(repo, SearchInput(catalog_id=summary.catalog_id, keyword="missing")).items
        == ()
    )


def test_numeric_uuid_exact_versions_and_pagination(repo):
    identities = [
        NOTICE,
        "10000002-01",
        "90000000-0000-0000-0000-000000000001-1",
        "90000000-0000-0000-0000-000000000001-01",
    ]
    summary = loaded(repo, archive([(i + ".json", package(i)) for i in identities]))
    first = search_catalog(
        repo, SearchInput(catalog_id=summary.catalog_id, keyword="software", limit=2)
    )
    second = search_catalog(
        repo, SearchInput(catalog_id=summary.catalog_id, keyword="software", limit=2, page=2)
    )
    assert first.next_page == 2 and second.next_page is None
    assert [r.external_id for r in (*first.items, *second.items)] == sorted(identities)
    assert len({opportunity_id(i) for i in identities}) == 4


def test_export_day_is_not_confused_with_utc_timestamp_calendar_date():
    body = package()
    body["publishedDate"] = "2026-10-29T23:30:00Z"
    catalog = parse_archive(archive([(NOTICE + ".json", body)]), date.fromisoformat(DAY))
    assert catalog.export_day == date(2026, 10, 30)
    assert catalog.records[0].original_metadata.published_at == "2026-10-29T23:30:00Z"


def test_missing_metadata_is_explicit_and_missing_title_not_importable(repo):
    body = package()
    body.pop("publishedDate")
    body["releases"][0]["buyer"] = {}
    body["releases"][0]["tender"] = {}
    summary = loaded(repo, archive([(NOTICE + ".json", body)]))
    catalog, _ = repo.get_bkms_catalog()
    assert summary.importable_count == 0
    assert set(catalog.records[0].mapping_warnings) == {
        "MISSING_TITLE",
        "MISSING_BUYER",
        "MISSING_DESCRIPTION",
        "MISSING_PUBLICATION_DATE",
        "MISSING_DEADLINE",
    }
    with pytest.raises(BkmsError, match="BKMS_NOT_IMPORTABLE"):
        repo.import_bkms_opportunity(ImportInput(catalog_id=summary.catalog_id, external_id=NOTICE))


@pytest.mark.parametrize(
    "mutate",
    [
        lambda p: p.update(license="PRIVATE"),
        lambda p: p.update(uri="https://example.invalid/PRIVATE"),
        lambda p: p.update(releases=[]),
        lambda p: p["releases"][0].update(id="wrong"),
        lambda p: p["releases"][0].update(tag="tender"),
        lambda p: p["releases"][0]["tender"].update(title={"PRIVATE": "value"}),
        lambda p: p.update(publishedDate="2026-99-99T00:00:00Z"),
    ],
)
def test_malformed_or_unlicensed_records_reject_complete_export(mutate):
    body = package()
    mutate(body)
    with pytest.raises(BkmsError):
        parse_archive(archive([(NOTICE + ".json", body)]), date.fromisoformat(DAY))


@pytest.mark.parametrize(
    "entry,body",
    [
        ("../10000001-1.json", b"{}"),
        ("notice.txt", b"{}"),
        (NOTICE + ".json", b'{"license":null,"license":null}'),
        (NOTICE + ".json", b"NaN"),
        (NOTICE + ".json", b"PRIVATE"),
    ],
)
def test_unsafe_entries_and_strict_json(entry, body):
    with pytest.raises(BkmsError, match="BKMS_INVALID_EXPORT"):
        parse_archive(archive([(entry, body)]), date.fromisoformat(DAY))


def test_empty_duplicate_and_archive_budgets(monkeypatch):
    from tendercite.services import bkms

    assert parse_archive(archive([]), date.fromisoformat(DAY)).records == ()
    with pytest.warns(UserWarning, match="Duplicate name"):
        duplicate = archive([(NOTICE + ".json", package()), (NOTICE + ".json", package())])
    with pytest.raises(BkmsError, match="BKMS_INVALID_EXPORT"):
        parse_archive(duplicate, date.fromisoformat(DAY))
    payload = archive()
    for name in ("MAX_DOWNLOAD_BYTES", "MAX_EXPANDED_BYTES", "MAX_RECORD_BYTES", "MAX_RECORDS"):
        with monkeypatch.context() as m:
            m.setattr(bkms, name, 0)
            with pytest.raises(BkmsError, match="BKMS_EXPORT_TOO_LARGE"):
                parse_archive(payload, date.fromisoformat(DAY))


@pytest.mark.parametrize(
    "status,code",
    [
        (302, "BKMS_UNAVAILABLE"),
        (400, "BKMS_INVALID_PUBLICATION_DAY"),
        (429, "BKMS_RATE_LIMITED"),
        (500, "BKMS_UNAVAILABLE"),
    ],
)
def test_provider_errors_no_redirect_or_retry(status, code):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(
            status, headers={"Location": "https://example.invalid/PRIVATE"}, text="PRIVATE"
        )

    with pytest.raises(BkmsError, match=code):
        provider(handler=handler).load(LoadInput(export_day=date.fromisoformat(DAY)))
    assert len(calls) == 1


def test_timeout_streaming_limit_and_invalid_archives(monkeypatch):
    from tendercite.services import bkms

    def timeout(request):
        raise httpx.ReadTimeout("PRIVATE", request=request)

    with pytest.raises(BkmsError, match="BKMS_TIMEOUT"):
        provider(handler=timeout).load(LoadInput(export_day=date.fromisoformat(DAY)))
    monkeypatch.setattr(bkms, "MAX_DOWNLOAD_BYTES", 1)
    with pytest.raises(BkmsError, match="BKMS_EXPORT_TOO_LARGE"):
        provider().load(LoadInput(export_day=date.fromisoformat(DAY)))
    with pytest.raises(BkmsError, match="BKMS_INVALID_EXPORT"):
        parse_archive(b"x", date.fromisoformat(DAY))


@pytest.mark.parametrize(
    "keyword", ["", " ", "A" * 151, 'Python" OR FT=*', "https://example.invalid", "*"]
)
def test_keyword_contract(keyword):
    with pytest.raises(ValidationError):
        SearchInput(catalog_id="a" * 64, keyword=keyword)


def test_local_range_guard_leaves_berlin_day_boundary_to_provider(monkeypatch):
    from datetime import UTC, datetime

    from tendercite.domain import bkms

    class Clock:
        @staticmethod
        def now(tz):
            return datetime(2026, 10, 30, 23, 30, tzinfo=UTC).astimezone(tz)

    monkeypatch.setattr(bkms, "datetime", Clock)
    assert LoadInput(export_day=date(2026, 10, 30)).export_day == date(2026, 10, 30)
    with pytest.raises(ValidationError):
        LoadInput(export_day=date(2026, 10, 31))


def test_duplicate_preserves_edits_restart_and_catalog_replacement(repo):
    summary = loaded(repo)
    request = ImportInput(catalog_id=summary.catalog_id, external_id=NOTICE)
    first = repo.import_bkms_opportunity(request)
    assert first.status == "CREATED" and first.record.opportunity.required_skills == ()
    assert first.record.opportunity.verification_status == "NOT_VERIFIED"
    assert first.record.opportunity.opportunity_type == "PUBLIC_TENDER"
    edited = repo.update_opportunity(
        first.record.opportunity_id,
        update_input(
            first.record.opportunity.model_copy(
                update={"title": "My local edit", "required_skills": ("Python",)}
            )
        ),
    )
    loaded(repo, archive([]))
    restarted = SqliteRepository(repo.database_path, initialize=False)
    repeated = restarted.import_bkms_opportunity(request)
    assert repeated.status == "ALREADY_PRESENT" and repeated.record == edited.record
    assert (
        repo.list_pipeline().entries == ()
        and repo.list_findings() == []
        and repo.list_profiles().profiles == ()
    )
    with pytest.raises(OpportunityError, match="PROVENANCE_CONFLICT"):
        repo.update_opportunity(
            first.record.opportunity_id,
            update_input(
                edited.record.opportunity.model_copy(
                    update={
                        "external_source": first.record.opportunity.external_source.model_copy(
                            update={"ocid": "changed"}
                        )
                    }
                ),
                revision=2,
            ),
        )


def test_conflict_concurrent_import_and_manual_collision(repo):
    summary = loaded(repo)
    request = ImportInput(catalog_id=summary.catalog_id, external_id=NOTICE)
    with pytest.raises(BkmsError, match="CATALOG_CONFLICT"):
        repo.import_bkms_opportunity(request.model_copy(update={"catalog_id": "a" * 64}))
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(
            pool.map(
                lambda _: (
                    SqliteRepository(repo.database_path, initialize=False)
                    .import_bkms_opportunity(request)
                    .status
                ),
                range(2),
            )
        ) == ["ALREADY_PRESENT", "CREATED"]
    other = "10000002-1"
    repo.create_opportunity(
        manual_input().model_copy(update={"opportunity_id": opportunity_id(other)})
    )
    with pytest.raises(OpportunityError, match="ID_CONFLICT"):
        repo.import_bkms_opportunity(request.model_copy(update={"external_id": other}))


def test_failed_download_keeps_catalog_and_tampering_is_detected(repo):
    summary = loaded(repo)
    before = repo.database_path.read_bytes()
    with pytest.raises(BkmsError):
        provider(b"PRIVATE").load(LoadInput(export_day=date.fromisoformat(DAY)))
    assert repo.database_path.read_bytes() == before
    with repo._connect() as c:
        c.execute("UPDATE bkms_catalog SET payload='{}'")
    with pytest.raises(BkmsError, match="STORAGE_INTEGRITY"):
        repo.get_bkms_catalog(summary.catalog_id)


def test_source_metadata_bounds_and_url_binding(repo):
    body = package()
    body["releases"][0]["tender"]["description"] = "x" * 9000
    summary = loaded(repo, archive([(NOTICE + ".json", body)]))
    record = repo.import_bkms_opportunity(
        ImportInput(catalog_id=summary.catalog_id, external_id=NOTICE)
    ).record
    source = record.opportunity.external_source
    assert (
        len(source.original_metadata.description) == 8000
        and len(record.opportunity.description) == 2000
    )
    assert {"SUMMARY_SHORTENED", "SOURCE_METADATA_SHORTENED"} <= set(source.mapping_warnings)
    for value in [
        source.record_url.replace("https:", "http:"),
        source.record_url.replace("10000001", "10000002"),
        source.record_url + "&url=PRIVATE",
    ]:
        with pytest.raises(ValueError):
            validate_record_url(value, NOTICE)
