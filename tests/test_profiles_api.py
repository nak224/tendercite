"""Offline native-profile API tests with synthetic declarations only."""

import asyncio
import json
import socket
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock
from uuid import UUID

import pytest
from fastapi import HTTPException
from profile_helpers import PROFILE_ID, create_body

from tendercite.api.dependencies import get_repository
from tendercite.core.config import Settings, settings
from tendercite.main import app

BASE = "/api/v1/profiles"
DETAIL = f"{BASE}/{PROFILE_ID}"


@pytest.fixture
def enabled(client, monkeypatch):
    monkeypatch.setattr(settings, "profiles_api_enabled", True)
    monkeypatch.setattr(settings, "capability_mapping_api_enabled", False)
    monkeypatch.setattr(settings, "capability_snapshot_api_enabled", False)
    return client, app.dependency_overrides[get_repository]()


def assert_safe(response, status, code):
    assert response.status_code == status
    assert response.json() == {"detail": {"code": code}}
    assert response.headers["cache-control"] == "no-store"
    for value in ("PRIVATE", "Traceback", "database_path", "skills", "profile_id"):
        assert value not in response.text


def update_body(profile, revision=1):
    return {
        "schema_version": "native-profile-update-1",
        "expected_revision": revision,
        "profile": profile,
    }


def test_disabled_by_default_before_body_or_repository_access(client, monkeypatch):
    monkeypatch.delenv("TENDERCITE_PROFILES_API_ENABLED", raising=False)
    assert Settings(_env_file=None).profiles_api_enabled is False
    monkeypatch.setattr(settings, "profiles_api_enabled", False)
    app.dependency_overrides[get_repository] = lambda: pytest.fail("Disabled profile DB access")
    for response in (
        client.get(BASE),
        client.get(DETAIL),
        client.post(BASE, content=b"PRIVATE"),
        client.put(DETAIL, content=b"PRIVATE"),
    ):
        assert_safe(response, 404, "PROFILES_API_DISABLED")


def test_create_list_read_update_and_skill_removal_without_snapshots(enabled):
    client, repo = enabled
    response = client.post(BASE, json=create_body())
    assert response.status_code == 201 and response.headers["cache-control"] == "no-store"
    created = response.json()["record"]
    assert created["schema_version"] == "native-profile-record-1" and created["revision"] == 1
    assert created["profile"]["skills"][0]["verification_status"] == "NOT_VERIFIED"
    listing = client.get(BASE).json()
    assert listing["profiles"][0]["name"] == "Synthetic freelancer"
    assert "skills" not in listing["profiles"][0]
    assert client.get(DETAIL).json() == created
    profile = created["profile"]
    profile.update(
        name="Synthetic company", profile_type="COMPANY", skills=[], opportunity_keywords=[]
    )
    response = client.put(DETAIL, json=update_body(profile))
    assert response.status_code == 200 and response.json()["status"] == "UPDATED"
    assert client.get(DETAIL).json()["profile"] == profile
    assert client.get(DETAIL).json()["revision"] == 2
    assert_safe(client.put(DETAIL, json=update_body(profile)), 409, "PROFILE_REVISION_CONFLICT")
    assert repo.list_capability_snapshots() == []
    assert repo.list_findings() == []


def test_idempotency_and_create_conflict_do_not_overwrite(enabled):
    client, repo = enabled
    first = client.post(BASE, json=create_body()).json()
    before = repo.database_path.read_bytes()
    repeated = client.post(BASE, json=create_body())
    assert repeated.status_code == 200 and repeated.json()["status"] == "ALREADY_PRESENT"
    assert repeated.json()["record"] == first["record"]
    assert repo.database_path.read_bytes() == before
    changed = create_body()
    changed["profile"]["name"] = "Other"
    assert_safe(client.post(BASE, json=changed), 409, "PROFILE_ID_CONFLICT")
    assert client.get(DETAIL).json() == first["record"]


@pytest.mark.parametrize(
    "change", ["missing-name", "extra", "duplicate", "bad-type", "bad-version", "path", "verified"]
)
def test_invalid_inputs_are_sanitized_for_create_and_update(enabled, change):
    client, repo = enabled
    body = create_body()
    if change == "missing-name":
        del body["profile"]["name"]
    if change == "extra":
        body["profile"]["PRIVATE"] = "PRIVATE"
    if change == "duplicate":
        body["profile"]["skills"] *= 2
    if change == "bad-type":
        body["profile"]["name"] = 42
    if change == "bad-version":
        body["profile"]["schema_version"] = "PRIVATE"
    if change == "path":
        body["profile"]["database_path"] = "/PRIVATE"
    if change == "verified":
        body["profile"]["qualification"] = "VERIFIED"
    before = repo.database_path.read_bytes()
    assert_safe(client.post(BASE, json=body), 422, "INVALID_PROFILE_INPUT")
    assert_safe(client.put(DETAIL, json=update_body(body["profile"])), 422, "INVALID_PROFILE_INPUT")
    assert repo.database_path.read_bytes() == before


@pytest.mark.parametrize("method,path", [("post", BASE), ("put", DETAIL)])
@pytest.mark.parametrize(
    "raw", [b'{"PRIVATE":', b'{"nested":{"x":1,"x":2}}', b'{"x":NaN}', b"\xff", b"[]"]
)
def test_bad_json_and_duplicate_keys_never_expose_contents(enabled, method, path, raw):
    client, _ = enabled
    assert_safe(
        getattr(client, method)(path, content=raw, headers={"Content-Type": "application/json"}),
        422,
        "INVALID_PROFILE_INPUT",
    )


@pytest.mark.parametrize("method,path", [("post", BASE), ("put", DETAIL)])
def test_transport_size_encoding_and_query_guards_are_shared(enabled, method, path):
    client, _ = enabled
    call = getattr(client, method)
    assert_safe(
        call(path, content=b" " * 65537, headers={"Content-Type": "application/json"}),
        413,
        "PROFILE_REQUEST_TOO_LARGE",
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
    assert_safe(call(path, json={}, params={"url": "PRIVATE"}), 422, "INVALID_PROFILE_INPUT")


def test_streamed_update_is_bounded_before_dependencies(enabled):
    app.dependency_overrides[get_repository] = lambda: pytest.fail("Oversized body DB access")
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
            pytest.fail("Read after crossing body limit")

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
    assert json.loads(sent[1]["body"]) == {"detail": {"code": "PROFILE_REQUEST_TOO_LARGE"}}


def test_pagination_invalid_ids_and_missing_records(enabled):
    client, _ = enabled
    for i in range(3):
        assert client.post(BASE, json=create_body(profile_id=str(UUID(int=i)))).status_code == 201
    first = client.get(BASE, params={"limit": 2}).json()
    assert len(first["profiles"]) == 2 and first["next_offset"] == 2
    assert len(client.get(BASE, params={"limit": 2, "offset": 2}).json()["profiles"]) == 1
    for query in ({"limit": 101}, {"offset": -1}, {"database": "PRIVATE"}):
        assert_safe(client.get(BASE, params=query), 422, "INVALID_PROFILE_INPUT")
    assert_safe(client.get(BASE + "/PRIVATE"), 422, "INVALID_PROFILE_INPUT")
    assert_safe(client.get(DETAIL), 404, "PROFILE_NOT_FOUND")
    assert_safe(
        client.put(DETAIL, json=update_body(create_body()["profile"])), 404, "PROFILE_NOT_FOUND"
    )


@pytest.mark.parametrize(
    "failure,status,code",
    [
        (sqlite3.OperationalError("PRIVATE"), 503, "DATABASE_FAILED"),
        (RuntimeError("PRIVATE"), 500, "PROFILE_OPERATION_FAILED"),
        (HTTPException(500, "PRIVATE"), 500, "PROFILE_OPERATION_FAILED"),
    ],
)
def test_private_errors_are_never_returned(enabled, monkeypatch, failure, status, code):
    client, repo = enabled
    monkeypatch.setattr(repo, "create_profile", Mock(side_effect=failure))
    assert_safe(client.post(BASE, json=create_body()), status, code)


def test_concurrent_http_edits_have_one_winner(enabled):
    client, _ = enabled
    profile = client.post(BASE, json=create_body()).json()["record"]["profile"]
    with ThreadPoolExecutor(max_workers=4) as pool:
        responses = list(
            pool.map(lambda _: client.put(DETAIL, json=update_body(profile)), range(4))
        )
    assert [r.status_code for r in responses].count(200) == 1
    assert [r.status_code for r in responses].count(409) == 3


def test_profile_requests_never_call_network_models_or_snapshot_services(enabled, monkeypatch):
    client, repo = enabled
    from tendercite.services.llm.openai_compatible import OpenAICompatibleLLM
    from tendercite.services.retrieval.embeddings import SentenceTransformerEmbeddingProvider

    def forbidden(*args, **kwargs):
        pytest.fail("Unexpected external/snapshot activity")

    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(OpenAICompatibleLLM, "request_completion", forbidden)
    monkeypatch.setattr(SentenceTransformerEmbeddingProvider, "__init__", forbidden)
    monkeypatch.setattr(repo, "import_capability_snapshot", forbidden)
    assert client.post(BASE, json=create_body()).status_code == 201
    assert client.get(DETAIL).status_code == 200


def test_openapi_exposes_versioned_contracts():
    paths = app.openapi()["paths"]
    assert "ProfileCreate" in str(paths[BASE]["post"]["requestBody"])
    assert "ProfileSaveResult" in str(paths[BASE]["post"]["responses"]["200"])
    assert "ProfileUpdate" in str(paths[BASE + "/{profile_id}"]["put"]["requestBody"])
