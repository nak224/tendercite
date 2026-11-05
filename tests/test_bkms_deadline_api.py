"""Synthetic HTTP/download adapters with the real local SQLite repository."""

from uuid import UUID

import httpx
import pytest
from bkms_deadline_helpers import eforms_archive, xml
from bkms_helpers import DAY, NOTICE, archive
from test_bkms_api import safe

from tendercite.api.dependencies import get_read_only_repository, get_repository
from tendercite.api.routes.bkms import _download_slot, get_bkms_client
from tendercite.core.config import settings
from tendercite.main import app
from tendercite.repositories.sqlite import SqliteRepository
from tendercite.services.bkms import EXPORT_URL, BkmsClient

BASE = "/api/v1/discovery/bkms"


@pytest.fixture
def ready(client, monkeypatch):
    monkeypatch.setattr(settings, "bkms_discovery_api_enabled", True)
    repo = app.dependency_overrides[get_repository]()
    app.dependency_overrides[get_read_only_repository] = lambda: SqliteRepository(
        repo.database_path, read_only=True
    )
    calls = []
    payload = {"xml": eforms_archive()}

    def handler(request):
        assert request.method == "GET" and str(request.url).split("?")[0] == EXPORT_URL
        assert request.url.params["pubDay"] == DAY
        calls.append(request.url.params["format"])
        if calls[-1] == "ocds.zip":
            return httpx.Response(200, content=archive())
        assert calls[-1] == "eforms.zip"
        return httpx.Response(200, content=payload["xml"])

    app.dependency_overrides[get_bkms_client] = lambda: BkmsClient(
        httpx.Client(transport=httpx.MockTransport(handler))
    )
    summary = client.post(BASE + "/load", json={"export_day": DAY}).json()
    return client, repo, summary, payload, calls


def request_body(summary, **changes):
    return {
        "schema_version": "bkms-deadline-load-1",
        "catalog_id": summary["catalog_id"],
        **changes,
    }


def test_disabled_before_body_or_dependencies(client, monkeypatch):
    monkeypatch.setattr(settings, "bkms_discovery_api_enabled", False)
    for dependency in (get_repository, get_read_only_repository, get_bkms_client):
        app.dependency_overrides[dependency] = lambda: pytest.fail("Unexpected access")
    safe(client.get(BASE + "/deadlines/" + "a" * 64), 404, "BKMS_DISCOVERY_API_DISABLED")
    safe(
        client.post(BASE + "/deadlines/load", content=b"PRIVATE"),
        404,
        "BKMS_DISCOVERY_API_DISABLED",
    )


def test_explicit_load_and_read_only_summary_search_current_evidence(ready):
    client, repo, summary, _, calls = ready
    before = repo.database_path.read_bytes()
    response = client.get(BASE + "/deadlines/" + summary["catalog_id"])
    assert response.status_code == 200 and response.json()["state"] == "NOT_LOADED"
    assert repo.database_path.read_bytes() == before and calls == ["ocds.zip"]
    loaded = client.post(BASE + "/deadlines/load", json=request_body(summary))
    assert loaded.status_code == 200 and loaded.headers["cache-control"] == "no-store"
    assert loaded.json()["state"] == "CURRENT" and loaded.json()["counts"] == {"SINGLE": 1}
    assert calls == ["ocds.zip", "eforms.zip"]
    before = repo.database_path.read_bytes()
    assert client.get(BASE + "/deadlines/" + summary["catalog_id"]).json() == loaded.json()
    coverage = client.get(BASE + "/catalogs").json()
    search = client.post(
        BASE + "/search",
        json={
            "schema_version": "bkms-search-input-2",
            "collection_id": coverage["collection_id"],
            "keyword": "Software",
        },
    ).json()
    source = search["items"][0]["deadline_enrichment"]
    assert source["catalog_id"] == summary["catalog_id"]
    assert source["notice"]["external_id"] == NOTICE
    assert source["notice"]["lots"][0]["periods"] == [
        {"dates": ["2026-12-04+01:00"], "times": ["10:00:00+01:00"]}
    ]
    assert "PRIVATE" not in str(search) and repo.database_path.read_bytes() == before
    assert calls == ["ocds.zip", "eforms.zip"]
    safe(
        client.post(BASE + "/deadlines/load", json=request_body(summary)),
        409,
        "BKMS_DEADLINE_CONFLICT",
    )
    assert len(calls) == 2
    reload = client.post(
        BASE + "/deadlines/load",
        json=request_body(summary, expected_enrichment_id=loaded.json()["enrichment_id"]),
    )
    assert reload.status_code == 200 and len(calls) == 3


def test_failure_keeps_previous_metadata_catalog_and_native_opportunity(ready):
    client, repo, summary, payload, calls = ready
    native = client.post(
        BASE + "/import", json={"catalog_id": summary["catalog_id"], "external_id": NOTICE}
    ).json()
    loaded = client.post(BASE + "/deadlines/load", json=request_body(summary)).json()
    body = request_body(summary, expected_enrichment_id=loaded["enrichment_id"])
    for raw, code in [
        (b"PRIVATE", "BKMS_INVALID_EFORMS"),
        (eforms_archive(xml("10000001-01"), "10000001-01"), "BKMS_EFORMS_IDENTITY_MISMATCH"),
    ]:
        payload["xml"] = raw
        before = repo.database_path.read_bytes()
        safe(client.post(BASE + "/deadlines/load", json=body), 502, code)
        assert repo.database_path.read_bytes() == before
        assert client.get(BASE + "/deadlines/" + summary["catalog_id"]).json() == loaded
    assert len(calls) == 4
    assert (
        repo.get_opportunity(UUID(native["record"]["opportunity_id"])).model_dump(mode="json")
        == native["record"]
    )


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"catalog_id": "PRIVATE"},
        {"catalog_id": "a" * 64, "url": "https://PRIVATE.invalid"},
        {"catalog_id": "a" * 64, "schema_version": "unsupported"},
        {"catalog_id": "a" * 64, "expected_enrichment_id": 12},
    ],
)
def test_bad_contract_is_safe_and_never_downloads(ready, body):
    client, _, _, _, calls = ready
    safe(client.post(BASE + "/deadlines/load", json=body), 422, "INVALID_BKMS_INPUT")
    assert calls == ["ocds.zip"]


def test_duplicate_keys_size_scope_conflicts_and_shared_busy_guard(ready):
    client, repo, summary, _, calls = ready
    safe(
        client.post(
            BASE + "/deadlines/load",
            content=b'{"catalog_id":"PRIVATE","catalog_id":"PRIVATE"}',
            headers={"Content-Type": "application/json"},
        ),
        422,
        "INVALID_BKMS_INPUT",
    )
    safe(
        client.post(
            BASE + "/deadlines/load",
            content=b" " * 65537,
            headers={"Content-Type": "application/json"},
        ),
        413,
        "BKMS_REQUEST_TOO_LARGE",
    )
    safe(
        client.post(BASE + "/deadlines/load", json={"catalog_id": "f" * 64}),
        409,
        "BKMS_CATALOG_CONFLICT",
    )
    with _download_slot:
        safe(client.post(BASE + "/deadlines/load", json=request_body(summary)), 409, "BKMS_BUSY")
    assert calls == ["ocds.zip"]
    loaded = client.post(BASE + "/deadlines/load", json=request_body(summary)).json()
    with repo._connect() as c:
        c.execute("UPDATE bkms_deadline_days SET content_hash=?", ("f" * 64,))
    safe(client.get(BASE + "/deadlines/" + summary["catalog_id"]), 409, "BKMS_STORAGE_INTEGRITY")
    assert loaded["state"] == "CURRENT"


def test_catalog_change_during_download_and_database_failure_are_safe(ready, monkeypatch):
    from datetime import date

    from tendercite.services.bkms import parse_archive

    client, repo, summary, _, _ = ready
    original = BkmsClient.download_day

    def racing_download(self, day, file_format):
        raw = original(self, day, file_format)
        repo.save_bkms_catalog(
            parse_archive(archive(), date.fromisoformat(DAY)),
            expected_catalog_id=summary["catalog_id"],
        )
        return raw

    monkeypatch.setattr(BkmsClient, "download_day", racing_download)
    safe(
        client.post(BASE + "/deadlines/load", json=request_body(summary)),
        409,
        "BKMS_CATALOG_CONFLICT",
    )
    with repo._connect() as c:
        assert c.execute("SELECT count(*) FROM bkms_deadline_days").fetchone()[0] == 0
        c.execute(
            "CREATE TRIGGER fail_deadline BEFORE INSERT ON bkms_deadline_days "
            "BEGIN SELECT RAISE(ABORT,'PRIVATE'); END"
        )
    monkeypatch.setattr(BkmsClient, "download_day", original)
    current = client.get(BASE + "/catalog").json()
    safe(client.post(BASE + "/deadlines/load", json=request_body(current)), 503, "DATABASE_FAILED")
    assert repo.get_bkms_deadline_summary(current["catalog_id"]).state == "NOT_LOADED"
