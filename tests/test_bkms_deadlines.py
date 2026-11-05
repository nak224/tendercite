"""Offline original fixtures, bounded XML handling and actual transactional SQLite."""

import io
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from xml.etree import ElementTree as ET

import httpx
import pytest
from bkms_deadline_helpers import eforms_archive, xml
from bkms_helpers import NOTICE, archive

from tendercite.domain.bkms import BkmsError, ImportInput
from tendercite.domain.bkms_catalogs import MultiSearchInput, RemoveInput
from tendercite.domain.bkms_deadlines import DeadlineLoadInput
from tendercite.domain.opportunities import OpportunityUpdate
from tendercite.repositories.sqlite import SqliteRepository
from tendercite.services import bkms_deadlines as parser
from tendercite.services.bkms import BkmsClient, parse_archive, search_catalogs

DAY = date(2026, 10, 30)


def parsed(body=None, identity=NOTICE, digest="a" * 64):
    return parser.parse_eforms_archive(eforms_archive(body, identity), DAY, digest)


@pytest.mark.parametrize(
    ("lots", "state", "warning"),
    [
        ([("LOT-0001", [("2026-12-04+01:00", "10:00:00+01:00")])], "SINGLE", None),
        ([("LOT-0001", [])], "MISSING", "MISSING_SUBMISSION_DEADLINE"),
        ([], "MISSING", "MISSING_SUBMISSION_DEADLINE"),
        ([("LOT-0001", [("2026-12-04", None)])], "SINGLE", "MISSING_TIME"),
        ([("LOT-0001", [("2026-12-04", "10:00")])], "SINGLE", "MISSING_TIMEZONE"),
        ([("LOT-0001", [("2026-12-04Z", "10:00:00+00:00")])], "SINGLE", None),
        ([("LOT-0001", [("2026-12-04+01:00", "10:00:00+02:00")])], "CONFLICT", "OFFSET_CONFLICT"),
        (
            [("LOT-0001", [("2026-12-04", "10:00"), ("2026-12-05", "10:00")])],
            "CONFLICT",
            "CONFLICTING_VALUES",
        ),
        ([("LOT-0001", [("2026-02-30", "10:00")])], "UNRECOGNIZED", "UNRECOGNIZED_STRUCTURE"),
        ([("LOT-0001", [("2026-12-04", "25:00")])], "UNRECOGNIZED", "UNRECOGNIZED_STRUCTURE"),
        (
            [("LOT-0001", [("2026-12-04", "10:00")]), ("LOT-0002", [("2026-12-05", "12:00")])],
            "MULTIPLE_LOTS",
            "MISSING_TIMEZONE",
        ),
        ([("LOT-0001", []), ("LOT-0001", [])], "CONFLICT", "DUPLICATE_LOT"),
    ],
)
def test_dates_lots_offsets_missing_and_conflicting_values(lots, state, warning):
    record = parsed(xml(lots=lots)).records[0]
    assert record.status == state
    if warning:
        assert warning in record.warnings
    for original, stored in zip(lots, record.lots, strict=True):
        assert original[0] == stored.lot_id
        for value, period in zip(original[1], stored.periods, strict=True):
            assert period.dates == (() if value[0] is None else (value[0],))
            assert period.times == (() if value[1] is None else (value[1],))


def test_exact_version_and_unsupported_customization_are_never_repaired():
    assert parsed(xml("10000001-01"), "10000001-01").records[0].external_id.endswith("-01")
    with pytest.raises(BkmsError, match="INVALID_EFORMS"):
        parsed(xml("10000001-01"))
    record = parsed(xml(customization="eforms-sdk-0.1")).records[0]
    assert record.status == "UNRECOGNIZED" and record.lots == ()
    assert record.warnings == ("UNSUPPORTED_CUSTOMIZATION",)


@pytest.mark.parametrize(
    "body",
    [
        b"<broken",
        b"<!DOCTYPE a [<!ENTITY x SYSTEM 'https://example.invalid/secret'>]><a>&x;</a>",
        b"<!DOCTYPE a [<!ENTITY a 'one'><!ENTITY b '&a;&a;'>]><a>&b;</a>",
        b"<?xml-stylesheet href='https://example.invalid/secret'?><a/>",
        b"<a xmlns='http://www.w3.org/2001/XInclude'><include href='/PRIVATE'/></a>",
        xml().decode().encode("utf-16"),
        b"<a>" * 65 + b"</a>" * 65,
        b"<a>" + b"<b/>" * 30001 + b"</a>",
        b"<unknown/>",
    ],
)
def test_unsafe_malformed_and_unbounded_xml_rejected_without_network(body, monkeypatch):
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", lambda *_: pytest.fail("Network"))
    with pytest.raises(BkmsError, match="INVALID_EFORMS"):
        parsed(body)


def test_unknown_paths_not_silently_missing_and_duplicate_xml_fields_not_selected():
    assert (
        parsed(xml().replace(b"TenderingProcess", b"UnknownProcess")).records[0].status
        == "UNRECOGNIZED"
    )
    root = ET.fromstring(xml())
    period = root.find(".//cac:TenderSubmissionDeadlinePeriod", parser.NS)
    ET.SubElement(period, f"{{{parser.NS['cbc']}}}EndDate").text = "2026-12-05+01:00"
    record = parsed(ET.tostring(root)).records[0]
    assert record.status == "CONFLICT" and len(record.lots[0].periods[0].dates) == 2


def test_archive_budgets_and_paths(monkeypatch):
    for name in ("../10000001-1.xml", "nested/10000001-1.xml", "10000001-1.json"):
        with pytest.raises(BkmsError, match="INVALID_EFORMS"):
            parser.parse_eforms_archive(archive([(name, xml())]), DAY, "a" * 64)
    with pytest.warns(UserWarning, match="Duplicate name"):
        duplicate = archive([(NOTICE + ".xml", xml())] * 2)
    with pytest.raises(BkmsError):
        parser.parse_eforms_archive(duplicate, DAY, "a" * 64)
    for budget in ("MAX_DOWNLOAD_BYTES", "MAX_EXPANDED_BYTES", "MAX_RECORD_BYTES", "MAX_RECORDS"):
        with monkeypatch.context() as m:
            m.setattr(parser, budget, 0)
            with pytest.raises(BkmsError, match="EXPORT_TOO_LARGE"):
                parsed()
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as z:
        info = zipfile.ZipInfo(NOTICE + ".xml")
        info.external_attr = 0o120777 << 16
        z.writestr(info, xml())
    with pytest.raises(BkmsError, match="INVALID_EFORMS"):
        parser.parse_eforms_archive(output.getvalue(), DAY, "a" * 64)


@pytest.fixture
def stored(tmp_path):
    repo = SqliteRepository(tmp_path / "synthetic.db")
    summary = repo.save_bkms_catalog(parse_archive(archive(), DAY))
    request = DeadlineLoadInput(catalog_id=summary.catalog_id)
    return repo, summary, request


def test_atomic_scope_provenance_stale_and_native_edits_are_preserved(stored):
    repo, summary, request = stored
    saved = repo.import_bkms_opportunity(
        ImportInput(catalog_id=summary.catalog_id, external_id=NOTICE)
    )
    edited = repo.update_opportunity(
        saved.record.opportunity_id,
        OpportunityUpdate(
            expected_revision=1,
            opportunity=saved.record.opportunity.model_copy(update={"deadline": date(2027, 1, 1)}),
        ),
    ).record
    before_catalog = repo.get_bkms_catalog(summary.catalog_id)
    assert repo.get_bkms_deadline_summary(summary.catalog_id).state == "NOT_LOADED"
    metadata = parsed(digest=summary.catalog_id)
    result = repo.save_bkms_deadlines(metadata, request)
    assert result.state == "CURRENT" and result.counts == {"SINGLE": 1}
    assert repo.get_opportunity(edited.opportunity_id) == edited
    assert repo.get_bkms_catalog(summary.catalog_id) == before_catalog
    assert "PRIVATE" not in metadata.model_dump_json()
    coverage = repo.get_bkms_catalogs()[1]
    readonly = SqliteRepository(repo.database_path, read_only=True)
    before = repo.database_path.read_bytes()
    hits = search_catalogs(
        readonly, MultiSearchInput(collection_id=coverage.collection_id, keyword="Software")
    )
    assert hits.items[0].deadline_enrichment.notice == metadata.records[0]
    assert repo.database_path.read_bytes() == before
    with pytest.raises(BkmsError, match="DEADLINE_CONFLICT"):
        repo.save_bkms_deadlines(metadata, request)
    replacement = repo.save_bkms_catalog(
        parse_archive(archive(), DAY), expected_catalog_id=summary.catalog_id
    )
    assert replacement.catalog_id != summary.catalog_id
    assert repo.get_bkms_deadline_summary(replacement.catalog_id).state == "STALE"
    assert not repo.get_bkms_deadline_evidence(repo.get_bkms_catalogs()[1])
    request = DeadlineLoadInput(
        catalog_id=replacement.catalog_id, expected_enrichment_id=result.enrichment_id
    )
    refreshed = repo.save_bkms_deadlines(parsed(digest=request.catalog_id), request)
    assert refreshed.state == "CURRENT"
    repo.remove_bkms_catalog(RemoveInput(export_day=DAY, expected_catalog_id=request.catalog_id))
    with repo._connect() as c:
        assert c.execute("SELECT count(*) FROM bkms_deadline_days").fetchone()[0] == 0
    assert repo.get_opportunity(edited.opportunity_id) == edited


def test_exact_complete_identity_set_failed_update_restart_and_concurrent_conflict(stored):
    repo, summary, request = stored
    before = repo.database_path.read_bytes()
    with pytest.raises(BkmsError, match="IDENTITY_MISMATCH"):
        repo.save_bkms_deadlines(
            parsed(xml("10000001-01"), "10000001-01", summary.catalog_id), request
        )
    assert repo.database_path.read_bytes() == before
    data = parsed(digest=summary.catalog_id)

    def save(_):
        try:
            return (
                SqliteRepository(repo.database_path, initialize=False)
                .save_bkms_deadlines(data, request)
                .state
            )
        except BkmsError as e:
            return e.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(save, (1, 2)))
    assert sorted(results) == ["BKMS_DEADLINE_CONFLICT", "CURRENT"]
    restarted = SqliteRepository(repo.database_path)
    assert restarted.get_bkms_deadline_summary(summary.catalog_id).state == "CURRENT"
    with restarted._connect() as c:
        c.execute("UPDATE bkms_deadline_days SET content_hash=?", ("f" * 64,))
    with pytest.raises(BkmsError, match="STORAGE_INTEGRITY"):
        restarted.get_bkms_deadline_summary(summary.catalog_id)


@pytest.mark.parametrize(
    "error,code",
    [
        (httpx.ReadTimeout("PRIVATE"), "BKMS_TIMEOUT"),
        (httpx.ConnectError("PRIVATE"), "BKMS_UNAVAILABLE"),
        (429, "BKMS_RATE_LIMITED"),
        (500, "BKMS_UNAVAILABLE"),
    ],
)
def test_download_failure_has_no_retry(error, code):
    calls = []

    def handler(r):
        calls.append(r)
        assert dict(r.url.params) == {"pubDay": DAY.isoformat(), "format": "eforms.zip"}
        if isinstance(error, Exception):
            raise error
        return httpx.Response(error)

    with BkmsClient(httpx.Client(transport=httpx.MockTransport(handler))) as client:
        with pytest.raises(BkmsError, match=code):
            client.download_day(DAY, "eforms.zip")
    assert len(calls) == 1


def test_failed_metadata_replace_rolls_back_and_no_cross_day_fallback(stored):
    from datetime import timedelta

    from tendercite.domain.opportunities import OpportunityError

    repo, summary, request = stored
    data = parsed(digest=summary.catalog_id)
    first = repo.save_bkms_deadlines(data, request)
    with repo._connect() as c:
        c.execute(
            "CREATE TRIGGER fail_deadline BEFORE UPDATE ON bkms_deadline_days "
            "BEGIN SELECT RAISE(ABORT,'PRIVATE'); END"
        )
    with pytest.raises(OpportunityError, match="DATABASE_FAILED"):
        repo.save_bkms_deadlines(
            data,
            DeadlineLoadInput(
                catalog_id=summary.catalog_id, expected_enrichment_id=first.enrichment_id
            ),
        )
    assert repo.get_bkms_deadline_summary(summary.catalog_id) == first
    # The same identity reappearing on another day never inherits an older day's enrichment.
    repo.save_bkms_catalog(parse_archive(archive(), DAY + timedelta(days=1)))
    hit = search_catalogs(
        repo,
        MultiSearchInput(
            collection_id=repo.get_bkms_catalogs()[1].collection_id, keyword="Software"
        ),
    ).items[0]
    assert hit.source.export_day != DAY and hit.deadline_enrichment is None
