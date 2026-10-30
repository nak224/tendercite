"""Synthetic public-provider responses and actual local SQLite API persistence."""

import httpx
import pytest
from discovery_helpers import NOTICE_ID, notice_payload, provider

from tendercite.api.dependencies import get_repository
from tendercite.api.routes.discovery import get_ted_client
from tendercite.core.config import Settings, settings
from tendercite.main import app

SEARCH = "/api/v1/discovery/ted/search"
IMPORT = "/api/v1/discovery/ted/import"


@pytest.fixture
def enabled(client, monkeypatch):
    monkeypatch.setattr(settings, "discovery_api_enabled", True)
    repo = app.dependency_overrides[get_repository]()
    app.dependency_overrides[get_ted_client] = lambda: provider()
    return client, repo


def safe(response, status, code):
    assert response.status_code == status
    assert response.json() == {"detail": {"code": code}}
    assert response.headers["cache-control"] == "no-store"
    assert "PRIVATE" not in response.text and "Traceback" not in response.text


def test_disabled_before_body_or_dependencies(client, monkeypatch):
    monkeypatch.delenv("TENDERCITE_DISCOVERY_API_ENABLED", raising=False)
    assert Settings(_env_file=None).discovery_api_enabled is False
    monkeypatch.setattr(settings, "discovery_api_enabled", False)
    app.dependency_overrides[get_repository] = lambda: pytest.fail("DB access")
    app.dependency_overrides[get_ted_client] = lambda: pytest.fail("Provider access")
    for path in (SEARCH, IMPORT):
        safe(client.post(path, content=b"PRIVATE"), 404, "DISCOVERY_API_DISABLED")


def test_search_has_no_repository_dependency_or_database_writes(enabled):
    client, repo = enabled
    before = repo.database_path.read_bytes()
    app.dependency_overrides[get_repository] = lambda: pytest.fail("Search initialized/wrote DB")
    response = client.post(
        SEARCH,
        json={"schema_version": "ted-search-input-1", "keyword": "Software", "limit": 2, "page": 1},
    )
    assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
    body = response.json()
    assert body["schema_version"] == "ted-search-result-1" and body["items"][0]["source"] == "TED"
    assert body["items"][0]["publication_id"] == NOTICE_ID
    assert repo.database_path.read_bytes() == before


def test_explicit_import_existing_api_compatibility_and_preserved_edits(enabled, monkeypatch):
    client, repo = enabled
    monkeypatch.setattr(settings, "opportunities_api_enabled", True)
    response = client.post(
        IMPORT, json={"schema_version": "ted-import-input-1", "publication_id": NOTICE_ID}
    )
    assert response.status_code == 201 and response.json()["status"] == "CREATED"
    record = response.json()["record"]
    identity = record["opportunity_id"]
    assert record["opportunity"]["source_origin"] == "ted_import"
    assert record["opportunity"]["external_source"]["external_id"] == NOTICE_ID
    assert record["opportunity"]["required_skills"] == []
    assert client.get(f"/api/v1/opportunities/{identity}").json() == record
    edited = record["opportunity"]
    edited["title"] = "Local title"
    edited["required_skills"] = ["Python"]
    assert (
        client.put(
            f"/api/v1/opportunities/{identity}",
            json={
                "schema_version": "native-opportunity-update-1",
                "expected_revision": 1,
                "opportunity": edited,
            },
        ).status_code
        == 200
    )
    duplicate = client.post(IMPORT, json={"publication_id": NOTICE_ID})
    assert duplicate.status_code == 200 and duplicate.json()["status"] == "ALREADY_PRESENT"
    assert (
        duplicate.json()["record"]["revision"] == 2
        and duplicate.json()["record"]["opportunity"]["title"] == "Local title"
    )
    changed = {
        **edited,
        "external_source": {**edited["external_source"], "retrieved_at": "2099-02-02T00:00:00Z"},
    }
    safe(
        client.put(
            f"/api/v1/opportunities/{identity}",
            json={"expected_revision": 2, "opportunity": changed},
        ),
        409,
        "OPPORTUNITY_PROVENANCE_CONFLICT",
    )
    safe(
        client.post(
            "/api/v1/opportunities", json={"opportunity_id": identity, "opportunity": edited}
        ),
        422,
        "INVALID_OPPORTUNITY_INPUT",
    )
    assert repo.list_pipeline().entries == () and repo.list_findings() == []


@pytest.mark.parametrize(
    "status,code",
    [
        (429, "TED_RATE_LIMITED"),
        (403, "TED_UNAVAILABLE"),
        (500, "TED_UNAVAILABLE"),
        (302, "TED_UNAVAILABLE"),
    ],
)
def test_safe_provider_errors_never_echo_private_response(enabled, status, code):
    client, _ = enabled
    app.dependency_overrides[get_ted_client] = lambda: provider(
        lambda _: httpx.Response(
            status,
            json={"PRIVATE": "PRIVATE"},
            headers={"Location": "https://example.invalid/PRIVATE"},
        )
    )
    safe(client.post(SEARCH, json={"keyword": "Software"}), 429 if status == 429 else 502, code)


def test_timeout_malformed_response_and_missing_notice(enabled):
    client, _ = enabled

    def timeout(request):
        raise httpx.ReadTimeout("PRIVATE", request=request)

    app.dependency_overrides[get_ted_client] = lambda: provider(timeout)
    safe(client.post(SEARCH, json={"keyword": "Software"}), 504, "TED_TIMEOUT")
    app.dependency_overrides[get_ted_client] = lambda: provider(
        lambda _: httpx.Response(200, text="PRIVATE")
    )
    safe(client.post(SEARCH, json={"keyword": "Software"}), 502, "TED_INVALID_RESPONSE")
    app.dependency_overrides[get_ted_client] = lambda: provider(
        lambda _: httpx.Response(200, json={"notices": [], "totalNoticeCount": 0})
    )
    safe(client.post(IMPORT, json={"publication_id": NOTICE_ID}), 404, "TED_NOTICE_NOT_FOUND")


@pytest.mark.parametrize(
    "path,body",
    [
        (SEARCH, {"keyword": 'x" OR 1=1'}),
        (SEARCH, {"keyword": "safe", "page": 51}),
        (SEARCH, {"keyword": "safe", "url": "https://PRIVATE"}),
        (IMPORT, {"publication_id": "PRIVATE"}),
        (IMPORT, {"publication_id": NOTICE_ID, "title": "Forged browser title"}),
    ],
)
def test_invalid_bodies_rejected_before_provider(enabled, path, body):
    client, _ = enabled
    app.dependency_overrides[get_ted_client] = lambda: pytest.fail("Invalid body touched provider")
    # Shared transport validates JSON structure before dependencies; typed fields may resolve
    # dependencies before model validation, so the client object itself must perform no network.
    app.dependency_overrides[get_ted_client] = lambda: provider(
        lambda _: pytest.fail("Invalid body made HTTP call")
    )
    safe(client.post(path, json=body), 422, "INVALID_DISCOVERY_INPUT")


def test_strict_json_and_transport_bounds(enabled):
    client, _ = enabled
    for path in (SEARCH, IMPORT):
        safe(
            client.post(
                path, content=b'{"x":1,"x":2}', headers={"Content-Type": "application/json"}
            ),
            422,
            "INVALID_DISCOVERY_INPUT",
        )
        safe(
            client.post(path, content=b" " * 65537, headers={"Content-Type": "application/json"}),
            413,
            "DISCOVERY_REQUEST_TOO_LARGE",
        )
        safe(
            client.post(path, content=b"{}", headers={"Content-Type": "text/plain"}),
            415,
            "UNSUPPORTED_MEDIA_TYPE",
        )


def test_no_title_cannot_be_fabricated_and_storage_failures_are_safe(enabled, monkeypatch):
    client, repo = enabled
    app.dependency_overrides[get_ted_client] = lambda: provider(
        lambda _: httpx.Response(
            200, json={"notices": [notice_payload(**{"notice-title": None})], "totalNoticeCount": 1}
        )
    )
    safe(client.post(IMPORT, json={"publication_id": NOTICE_ID}), 422, "TED_NOTICE_NOT_IMPORTABLE")

    def broken(*args):
        raise RuntimeError("PRIVATE")

    monkeypatch.setattr(repo, "find_ted_opportunity", broken)
    safe(client.post(IMPORT, json={"publication_id": NOTICE_ID}), 500, "DISCOVERY_OPERATION_FAILED")
