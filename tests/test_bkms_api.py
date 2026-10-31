"""Official-source boundary with synthetic provider bytes and real SQLite transactions."""

import sqlite3

import httpx
import pytest
from bkms_helpers import DAY, NOTICE, provider
from discovery_helpers import NOTICE_ID as TED_ID
from discovery_helpers import provider as ted_provider
from profile_helpers import create_input as profile_input

from tendercite.api.dependencies import get_read_only_repository, get_repository
from tendercite.api.routes.bkms import _download_slot, get_bkms_client
from tendercite.core.config import Settings, settings
from tendercite.domain.opportunities import OpportunityUpdate
from tendercite.main import app
from tendercite.repositories.sqlite import SqliteRepository
from tendercite.services.discovery import import_ted

BASE = "/api/v1/discovery/bkms"


@pytest.fixture
def enabled(client, monkeypatch):
    monkeypatch.setattr(settings, "bkms_discovery_api_enabled", True)
    repo = app.dependency_overrides[get_repository]()
    app.dependency_overrides[get_read_only_repository] = lambda: SqliteRepository(
        repo.database_path, read_only=True
    )
    app.dependency_overrides[get_bkms_client] = lambda: provider()
    return client, repo


def safe(response, status, code):
    assert response.status_code == status
    assert response.json() == {"detail": {"code": code}}
    assert response.headers["cache-control"] == "no-store"
    assert "PRIVATE" not in response.text and "Traceback" not in response.text


def load(client):
    response = client.post(
        BASE + "/load", json={"schema_version": "bkms-load-input-1", "export_day": DAY}
    )
    assert response.status_code == 200
    return response.json()


def import_body(catalog):
    return {
        "schema_version": "bkms-import-input-1",
        "catalog_id": catalog["catalog_id"],
        "external_id": NOTICE,
    }


def test_disabled_by_default_independent_of_ted_before_dependencies(client, monkeypatch):
    monkeypatch.delenv("TENDERCITE_BKMS_DISCOVERY_API_ENABLED", raising=False)
    assert Settings(_env_file=None).bkms_discovery_api_enabled is False
    monkeypatch.setattr(settings, "bkms_discovery_api_enabled", False)
    monkeypatch.setattr(settings, "discovery_api_enabled", True)
    for dependency in (get_repository, get_read_only_repository, get_bkms_client):
        app.dependency_overrides[dependency] = lambda: pytest.fail("Unexpected access")
    safe(client.get(BASE + "/catalog"), 404, "BKMS_DISCOVERY_API_DISABLED")
    for operation in ("load", "search", "import"):
        safe(
            client.post(BASE + "/" + operation, content=b"PRIVATE"),
            404,
            "BKMS_DISCOVERY_API_DISABLED",
        )


def test_local_reads_search_and_import_never_contact_provider_or_mutate_other_tables(
    enabled, monkeypatch
):
    client, repo = enabled
    safe(client.get(BASE + "/catalog"), 404, "BKMS_CATALOG_NOT_FOUND")
    catalog = load(client)
    app.dependency_overrides[get_bkms_client] = lambda: pytest.fail("Unexpected provider access")
    monkeypatch.setattr(
        httpx.HTTPTransport, "handle_request", lambda *_: pytest.fail("Unexpected network")
    )
    before = repo.database_path.read_bytes()
    assert client.get(BASE + "/catalog").json() == catalog
    result = client.post(
        BASE + "/search",
        json={"catalog_id": catalog["catalog_id"], "keyword": "SOFTWARE", "limit": 1},
    ).json()
    assert result["items"][0]["external_id"] == NOTICE and result["catalog"] == catalog
    assert repo.database_path.read_bytes() == before
    created = client.post(BASE + "/import", json=import_body(catalog))
    assert created.status_code == 201 and created.json()["status"] == "CREATED"
    opportunity = created.json()["record"]["opportunity"]
    assert opportunity["source_origin"] == "bkms_import"
    assert opportunity["external_source"]["source"] == "BEKANNTMACHUNGSSERVICE"
    assert (
        opportunity["external_source"]["original_metadata"]["title"]
        == "Synthetic software procurement"
    )
    assert (
        opportunity["required_skills"] == []
        and opportunity["verification_status"] == "NOT_VERIFIED"
    )
    assert (
        client.post(BASE + "/import", json=import_body(catalog)).json()["status"]
        == "ALREADY_PRESENT"
    )
    assert (
        repo.list_findings() == []
        and repo.list_pipeline().entries == ()
        and repo.list_profiles().profiles == ()
    )
    assert repo.list_capability_snapshots() == []


@pytest.mark.parametrize(
    "operation,body,code",
    [
        ("load", {"export_day": "1900-01-01"}, "INVALID_BKMS_INPUT"),
        ("load", {"export_day": "2099-01-01"}, "INVALID_BKMS_INPUT"),
        ("load", {"export_day": DAY, "url": "PRIVATE"}, "INVALID_BKMS_INPUT"),
        ("load", {"export_day": DAY, "pubMonth": "2026-10"}, "INVALID_BKMS_INPUT"),
        ("search", {"catalog_id": "a" * 64, "keyword": '"PRIVATE"'}, "INVALID_BKMS_INPUT"),
        ("import", {"catalog_id": "a" * 64, "external_id": "../../PRIVATE"}, "INVALID_BKMS_INPUT"),
        (
            "import",
            {"catalog_id": "a" * 64, "external_id": "-------------------------------------1"},
            "INVALID_BKMS_INPUT",
        ),
    ],
)
def test_safe_invalid_requests(enabled, operation, body, code):
    client, _ = enabled
    safe(client.post(BASE + "/" + operation, json=body), 422, code)


def test_bounded_duplicate_key_and_media_validation(enabled):
    client, _ = enabled
    safe(
        client.post(
            BASE + "/load",
            content=b'{"export_day":"PRIVATE","export_day":"PRIVATE"}',
            headers={"Content-Type": "application/json"},
        ),
        422,
        "INVALID_BKMS_INPUT",
    )
    safe(
        client.post(
            BASE + "/load", content=b" " * 65537, headers={"Content-Type": "application/json"}
        ),
        413,
        "BKMS_REQUEST_TOO_LARGE",
    )
    safe(
        client.post(BASE + "/load", content=b"PRIVATE", headers={"Content-Type": "text/plain"}),
        415,
        "UNSUPPORTED_MEDIA_TYPE",
    )


def test_failed_exports_stale_catalog_and_busy_preserve_existing_data(enabled):
    client, repo = enabled
    first = load(client)
    before = repo.database_path.read_bytes()
    app.dependency_overrides[get_bkms_client] = lambda: provider(b"PRIVATE")
    safe(client.post(BASE + "/load", json={"export_day": DAY}), 502, "BKMS_INVALID_EXPORT")
    assert repo.database_path.read_bytes() == before
    assert client.get(BASE + "/catalog").json() == first
    safe(
        client.post(BASE + "/search", json={"catalog_id": "0" * 64, "keyword": "Software"}),
        409,
        "BKMS_CATALOG_CONFLICT",
    )
    safe(
        client.post(BASE + "/import", json={**import_body(first), "external_id": "10000002-1"}),
        404,
        "BKMS_NOTICE_NOT_FOUND",
    )
    _download_slot.acquire()
    try:
        safe(client.post(BASE + "/load", json={"export_day": DAY}), 409, "BKMS_BUSY")
    finally:
        _download_slot.release()


def test_provider_and_storage_errors_safe(enabled, monkeypatch):
    client, repo = enabled

    def failure(request):
        return httpx.Response(429, text="PRIVATE")

    app.dependency_overrides[get_bkms_client] = lambda: provider(handler=failure)
    safe(client.post(BASE + "/load", json={"export_day": DAY}), 429, "BKMS_RATE_LIMITED")

    def database_failure(*_):
        raise sqlite3.OperationalError("PRIVATE /database/path")

    monkeypatch.setattr(repo, "import_bkms_opportunity", database_failure)
    safe(
        client.post(BASE + "/import", json={"catalog_id": "a" * 64, "external_id": NOTICE}),
        503,
        "DATABASE_FAILED",
    )


def test_native_editor_matching_pipeline_and_ted_remain_independent(enabled, monkeypatch):
    from uuid import UUID

    from tendercite.domain.native_matching import NativeMatchSelection
    from tendercite.domain.pipeline import PipelineCreate
    from tendercite.services.native_matching import preview_native_match

    client, repo = enabled
    catalog = load(client)
    record = client.post(BASE + "/import", json=import_body(catalog)).json()["record"]
    saved = repo.get_opportunity(UUID(record["opportunity_id"]))
    changed = repo.update_opportunity(
        saved.opportunity_id,
        OpportunityUpdate(
            expected_revision=1,
            opportunity=saved.opportunity.model_copy(
                update={"title": "My edit", "required_skills": ("Python",)}
            ),
        ),
    )
    repeated = client.post(BASE + "/import", json=import_body(catalog)).json()["record"]
    assert repeated == changed.record.model_dump(mode="json")
    assert repeated["opportunity"]["external_source"] == record["opportunity"]["external_source"]
    profile = repo.create_profile(profile_input()).record
    selection = NativeMatchSelection(
        profile_id=profile.profile_id,
        opportunity_id=saved.opportunity_id,
        expected_profile_revision=1,
        expected_opportunity_revision=2,
    )
    result = preview_native_match(selection, repository=repo)
    assert result.counts.matched_skills == 1 and result.qualification == "NOT_VERIFIED"
    pipeline = repo.create_pipeline_entry(
        PipelineCreate(pipeline_id=UUID(int=2300), opportunity_id=saved.opportunity_id)
    )
    assert pipeline.entry.record.values.stage == "SAVED"
    ted = import_ted(TED_ID, repo, ted_provider()).record
    assert (
        ted.opportunity_id != saved.opportunity_id and ted.opportunity.source_origin == "ted_import"
    )
    assert len(repo.list_opportunities().opportunities) == 2
