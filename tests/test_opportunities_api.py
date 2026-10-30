"""Offline native opportunity API coverage using only synthetic references."""

import asyncio
import json
import socket
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock
from uuid import UUID

import httpx
import pytest
from fastapi import HTTPException
from opportunity_helpers import OPPORTUNITY_ID, create_body

from tendercite.api.dependencies import get_repository
from tendercite.core.config import Settings, settings
from tendercite.main import app

BASE = "/api/v1/opportunities"
DETAIL = f"{BASE}/{OPPORTUNITY_ID}"


@pytest.fixture
def enabled(client, monkeypatch):
    monkeypatch.setattr(settings, "opportunities_api_enabled", True)
    monkeypatch.setattr(settings, "profiles_api_enabled", False)
    monkeypatch.setattr(settings, "capability_mapping_api_enabled", False)
    return client, app.dependency_overrides[get_repository]()


def assert_safe(response, status, code):
    assert response.status_code == status
    assert response.json() == {"detail": {"code": code}}
    assert response.headers["cache-control"] == "no-store"
    for value in ("PRIVATE", "Traceback", "database_path", "source_url", "required_skills"):
        assert value not in response.text


def update_body(value, revision=1):
    return {
        "schema_version": "native-opportunity-update-1",
        "expected_revision": revision,
        "opportunity": value,
    }


def test_disabled_by_default_before_body_or_dependency_access(client, monkeypatch):
    monkeypatch.delenv("TENDERCITE_OPPORTUNITIES_API_ENABLED", raising=False)
    assert Settings(_env_file=None).opportunities_api_enabled is False
    monkeypatch.setattr(settings, "opportunities_api_enabled", False)
    app.dependency_overrides[get_repository] = lambda: pytest.fail("Disabled DB access")
    for response in (
        client.get(BASE),
        client.get(DETAIL),
        client.post(BASE, content=b"PRIVATE"),
        client.put(DETAIL, content=b"PRIVATE"),
    ):
        assert_safe(response, 404, "OPPORTUNITIES_API_DISABLED")


@pytest.mark.parametrize("kind", ["FREELANCE_PROJECT", "PUBLIC_TENDER", "OTHER"])
def test_create_list_retrieve_update_skills_and_manual_boundaries(enabled, kind):
    client, repo = enabled
    response = client.post(BASE, json=create_body(kind))
    assert response.status_code == 201 and response.headers["cache-control"] == "no-store"
    result = response.json()["record"]
    assert (
        result["revision"] == 1 and result["opportunity"]["verification_status"] == "NOT_VERIFIED"
    )
    assert client.get(DETAIL).json() == result
    metadata = client.get(BASE).json()["opportunities"][0]
    assert metadata["title"] == "Synthetic Python project" and metadata["opportunity_type"] == kind
    assert (
        metadata["organization"] == "Synthetic organization"
        and metadata["deadline"] == "2027-03-01"
    )
    assert "source_url" not in metadata and "required_skills" not in metadata
    opportunity = result["opportunity"]
    opportunity.update(
        title="Changed synthetic project", required_skills=["React"], source_url="", deadline=None
    )
    updated = client.put(DETAIL, json=update_body(opportunity))
    assert updated.status_code == 200 and updated.json()["status"] == "UPDATED"
    assert client.get(DETAIL).json()["opportunity"] == opportunity
    assert_safe(
        client.put(DETAIL, json=update_body(opportunity)), 409, "OPPORTUNITY_REVISION_CONFLICT"
    )
    assert repo.list_findings() == [] and repo.list_profiles().profiles == ()


def test_identical_creation_and_conflicting_content_never_overwrite(enabled):
    client, repo = enabled
    first = client.post(BASE, json=create_body()).json()["record"]
    before = repo.database_path.read_bytes()
    repeated = client.post(BASE, json=create_body())
    assert repeated.status_code == 200 and repeated.json()["status"] == "ALREADY_PRESENT"
    assert repeated.json()["record"] == first and repo.database_path.read_bytes() == before
    body = create_body()
    body["opportunity"]["title"] = "Conflicting content"
    assert_safe(client.post(BASE, json=body), 409, "OPPORTUNITY_ID_CONFLICT")
    assert client.get(DETAIL).json() == first


@pytest.mark.parametrize(
    "change",
    [
        {"title": " "},
        {"title": 42},
        {"required_skills": ["Python", "python"]},
        {"opportunity_type": "PRIVATE"},
        {"source_url": "javascript:PRIVATE"},
        {"source_url": "https://user:PRIVATE@example.invalid"},
        {"deadline": "2027-02-29"},
        {"analysis_id": "PRIVATE"},
        {"profile_id": "PRIVATE"},
    ],
)
def test_invalid_contract_safe_for_create_and_update_without_writes(enabled, change):
    client, repo = enabled
    body = create_body()
    body["opportunity"].update(change)
    before = repo.database_path.read_bytes()
    assert_safe(client.post(BASE, json=body), 422, "INVALID_OPPORTUNITY_INPUT")
    assert_safe(
        client.put(DETAIL, json=update_body(body["opportunity"])), 422, "INVALID_OPPORTUNITY_INPUT"
    )
    assert repo.database_path.read_bytes() == before


@pytest.mark.parametrize("method,path", [("post", BASE), ("put", DETAIL)])
@pytest.mark.parametrize(
    "raw", [b'{"PRIVATE":', b'{"nested":{"x":1,"x":2}}', b'{"x":NaN}', b"\xff", b"[]"]
)
def test_malformed_json_and_duplicate_keys_rejected(enabled, method, path, raw):
    client, _ = enabled
    assert_safe(
        getattr(client, method)(path, content=raw, headers={"Content-Type": "application/json"}),
        422,
        "INVALID_OPPORTUNITY_INPUT",
    )


@pytest.mark.parametrize("method,path", [("post", BASE), ("put", DETAIL)])
def test_transport_guards_are_bounded_and_fixed(enabled, method, path):
    client, _ = enabled
    call = getattr(client, method)
    assert_safe(
        call(path, content=b" " * 65537, headers={"Content-Type": "application/json"}),
        413,
        "OPPORTUNITY_REQUEST_TOO_LARGE",
    )
    assert_safe(
        call(path, content=b"{}", headers={"Content-Type": "text/plain"}),
        415,
        "UNSUPPORTED_MEDIA_TYPE",
    )
    assert_safe(
        call(
            path,
            content=b"{}",
            headers={"Content-Type": "application/json", "Content-Encoding": "gzip"},
        ),
        415,
        "UNSUPPORTED_CONTENT_ENCODING",
    )
    assert_safe(call(path, params={"url": "PRIVATE"}, json={}), 422, "INVALID_OPPORTUNITY_INPUT")


def test_streamed_request_without_content_length_stops_before_dependency_access(enabled):
    app.dependency_overrides[get_repository] = lambda: pytest.fail("Oversized DB access")
    messages = iter(
        [
            {"type": "http.request", "body": b" " * 65536, "more_body": True},
            {"type": "http.request", "body": b" ", "more_body": True},
        ]
    )
    sent = []

    async def receive():
        try:
            return next(messages)
        except StopIteration:
            pytest.fail("Read after size limit")

    async def send(message):
        sent.append(message)

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "PUT",
        "scheme": "http",
        "path": DETAIL,
        "query_string": b"",
        "headers": [(b"content-type", b"application/json")],
        "server": ("testserver", 80),
        "client": ("testclient", 123),
    }
    asyncio.run(app(scope, receive, send))
    assert sent[0]["status"] == 413
    assert json.loads(sent[1]["body"]) == {"detail": {"code": "OPPORTUNITY_REQUEST_TOO_LARGE"}}


def test_bounded_pagination_missing_records_and_invalid_identity(enabled):
    client, _ = enabled
    for i in range(3):
        assert (
            client.post(BASE, json=create_body(opportunity_id=str(UUID(int=i)))).status_code == 201
        )
    first = client.get(BASE, params={"limit": 2}).json()
    assert len(first["opportunities"]) == 2 and first["next_offset"] == 2
    assert len(client.get(BASE, params={"limit": 2, "offset": 2}).json()["opportunities"]) == 1
    for params in ({"limit": 101}, {"offset": -1}, {"database": "PRIVATE"}):
        assert_safe(client.get(BASE, params=params), 422, "INVALID_OPPORTUNITY_INPUT")
    assert_safe(client.get(BASE + "/PRIVATE"), 422, "INVALID_OPPORTUNITY_INPUT")
    assert_safe(client.get(DETAIL), 404, "OPPORTUNITY_NOT_FOUND")


@pytest.mark.parametrize(
    "failure,status,code",
    [
        (sqlite3.OperationalError("PRIVATE"), 503, "DATABASE_FAILED"),
        (RuntimeError("PRIVATE"), 500, "OPPORTUNITY_OPERATION_FAILED"),
        (HTTPException(500, "PRIVATE"), 500, "OPPORTUNITY_OPERATION_FAILED"),
    ],
)
def test_storage_and_unexpected_failures_are_private(enabled, monkeypatch, failure, status, code):
    client, repo = enabled
    monkeypatch.setattr(repo, "create_opportunity", Mock(side_effect=failure))
    assert_safe(client.post(BASE, json=create_body()), status, code)


def test_concurrent_api_updates_have_one_winner(enabled):
    client, _ = enabled
    value = client.post(BASE, json=create_body()).json()["record"]["opportunity"]
    with ThreadPoolExecutor(max_workers=4) as pool:
        responses = list(pool.map(lambda _: client.put(DETAIL, json=update_body(value)), range(4)))
    assert [r.status_code for r in responses].count(200) == 1
    assert [r.status_code for r in responses].count(409) == 3


def test_no_network_url_fetching_models_or_analysis_invocation(enabled, monkeypatch):
    client, _ = enabled
    from tendercite.services.llm.openai_compatible import OpenAICompatibleLLM
    from tendercite.services.retrieval.embeddings import SentenceTransformerEmbeddingProvider

    def forbidden(*args, **kwargs):
        pytest.fail("Unexpected network/model access")

    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", forbidden)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", forbidden)
    monkeypatch.setattr(OpenAICompatibleLLM, "request_completion", forbidden)
    monkeypatch.setattr(SentenceTransformerEmbeddingProvider, "__init__", forbidden)
    assert client.post(BASE, json=create_body("PUBLIC_TENDER")).status_code == 201
    assert client.get(DETAIL).status_code == 200


def test_openapi_versioned_request_and_response_contracts():
    paths = app.openapi()["paths"]
    assert "OpportunityCreate" in str(paths[BASE]["post"]["requestBody"])
    assert "OpportunityUpdate" in str(paths[BASE + "/{opportunity_id}"]["put"]["requestBody"])
    assert "OpportunitySaveResult" in str(paths[BASE]["post"]["responses"]["200"])
