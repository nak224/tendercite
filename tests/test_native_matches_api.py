"""Read-only native matching HTTP boundary; no external data or services."""

import asyncio
import json
import socket
import sqlite3

import httpx
import pytest
from fastapi import HTTPException
from test_native_matching import records

from tendercite.api.dependencies import get_read_only_repository, get_repository
from tendercite.core.config import Settings, settings
from tendercite.main import app
from tendercite.repositories.sqlite import SqliteRepository

BASE = "/api/v1/native-matches/preview"


@pytest.fixture
def enabled(client, monkeypatch):
    monkeypatch.setattr(settings, "native_matching_api_enabled", True)
    repo = app.dependency_overrides[get_repository]()
    _, _, selection = records(repo, ("Python", "Java"), ("python", "JavaScript"))
    app.dependency_overrides[get_read_only_repository] = lambda: SqliteRepository(
        repo.database_path, read_only=True
    )
    return client, repo, selection.model_dump(mode="json")


def safe(response, status, code):
    assert response.status_code == status
    assert response.json() == {"detail": {"code": code}}
    assert response.headers["cache-control"] == "no-store"
    assert "PRIVATE" not in response.text and "Traceback" not in response.text


def test_disabled_before_body_and_database_access(client, monkeypatch):
    monkeypatch.delenv("TENDERCITE_NATIVE_MATCHING_API_ENABLED", raising=False)
    assert Settings(_env_file=None).native_matching_api_enabled is False
    monkeypatch.setattr(settings, "native_matching_api_enabled", False)
    app.dependency_overrides[get_read_only_repository] = lambda: pytest.fail("Database access")
    safe(client.post(BASE, content=b"PRIVATE"), 404, "NATIVE_MATCHING_API_DISABLED")


def test_preview_metadata_and_no_network_or_mutation(enabled, monkeypatch):
    client, repo, body = enabled

    def forbidden(*args, **kwargs):
        pytest.fail("Unexpected service or network")

    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", forbidden)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", forbidden)
    from tendercite.api import dependencies

    monkeypatch.setattr(dependencies, "get_llm", forbidden)
    monkeypatch.setattr(dependencies, "get_retrieval", forbidden)
    before = repo.database_path.read_bytes()
    response = client.post(BASE, json=body)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    result = response.json()
    assert result["schema_version"] == "native-match-result-1"
    assert result["algorithm_version"] == "nfkc-casefold-whitespace-equality-1"
    assert result["profile"]["profile_id"] == body["profile_id"]
    assert result["opportunity"]["opportunity_id"] == body["opportunity_id"]
    assert result["counts"] == {
        "total_opportunity_skills": 2,
        "matched_skills": 1,
        "not_recorded_skills": 1,
    }
    assert result["qualification"] == result["eligibility"] == "NOT_VERIFIED"
    assert result["availability"] == "NOT_EVALUATED"
    assert repo.database_path.read_bytes() == before
    paths = app.openapi()["paths"]
    assert "NativeMatchSelection" in str(paths[BASE]["post"]["requestBody"])
    assert "NativeMatchResult" in str(paths[BASE]["post"]["responses"]["200"])


@pytest.mark.parametrize(
    "change",
    [
        {"profile_id": "PRIVATE"},
        {"opportunity_id": "PRIVATE"},
        {"schema_version": "PRIVATE"},
        {"expected_profile_revision": 0},
        {"expected_opportunity_revision": True},
        {"profile": {"PRIVATE": "PRIVATE"}},
        {"url": "https://PRIVATE"},
        {"database_path": "/PRIVATE"},
    ],
)
def test_invalid_contract_never_echoes_input(enabled, change):
    client, repo, body = enabled
    before = repo.database_path.read_bytes()
    safe(client.post(BASE, json={**body, **change}), 422, "INVALID_NATIVE_MATCH_INPUT")
    assert repo.database_path.read_bytes() == before


@pytest.mark.parametrize(
    "field,code",
    [
        ("profile_id", "PROFILE_NOT_FOUND"),
        ("opportunity_id", "OPPORTUNITY_NOT_FOUND"),
        ("expected_profile_revision", "PROFILE_REVISION_CONFLICT"),
        ("expected_opportunity_revision", "OPPORTUNITY_REVISION_CONFLICT"),
    ],
)
def test_missing_or_stale_selections(enabled, field, code):
    client, _, body = enabled
    body[field] = "00000000-0000-0000-0000-000000000001" if field.endswith("_id") else 2
    safe(client.post(BASE, json=body), 404 if field.endswith("_id") else 409, code)


@pytest.mark.parametrize("raw", [b'{"x":1,"x":2}', b'{"x":NaN}', b'{"PRIVATE":', b"[]", b"\xff"])
def test_malformed_json(enabled, raw):
    client, _, _ = enabled
    safe(
        client.post(BASE, content=raw, headers={"Content-Type": "application/json"}),
        422,
        "INVALID_NATIVE_MATCH_INPUT",
    )


def test_size_encoding_and_query_guards(enabled):
    client, _, body = enabled
    safe(
        client.post(BASE, content=b" " * 65537, headers={"Content-Type": "application/json"}),
        413,
        "NATIVE_MATCH_REQUEST_TOO_LARGE",
    )
    safe(
        client.post(BASE, content=b"{}", headers={"Content-Type": "text/plain"}),
        415,
        "UNSUPPORTED_MEDIA_TYPE",
    )
    safe(
        client.post(
            BASE,
            content=b"{}",
            headers={"Content-Type": "application/json", "Content-Encoding": "gzip"},
        ),
        415,
        "UNSUPPORTED_CONTENT_ENCODING",
    )
    safe(
        client.post(BASE, json=body, params={"PRIVATE": "PRIVATE"}),
        422,
        "INVALID_NATIVE_MATCH_INPUT",
    )


def test_stream_without_content_length_is_bounded_before_repository(enabled):
    app.dependency_overrides[get_read_only_repository] = lambda: pytest.fail("DB access")
    messages = iter(
        [
            {"type": "http.request", "body": b" " * 65536, "more_body": True},
            {"type": "http.request", "body": b" ", "more_body": True},
        ]
    )
    sent = []

    async def receive():
        return next(messages)

    async def send(message):
        sent.append(message)

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": BASE,
        "query_string": b"",
        "headers": [(b"content-type", b"application/json")],
        "server": ("testserver", 80),
        "client": ("testclient", 123),
    }
    asyncio.run(app(scope, receive, send))
    assert sent[0]["status"] == 413
    assert json.loads(sent[1]["body"])["detail"]["code"] == "NATIVE_MATCH_REQUEST_TOO_LARGE"


@pytest.mark.parametrize(
    "error,code,status",
    [
        (sqlite3.OperationalError("PRIVATE"), "DATABASE_FAILED", 503),
        (RuntimeError("PRIVATE"), "NATIVE_MATCH_FAILED", 500),
        (HTTPException(500, "PRIVATE"), "NATIVE_MATCH_FAILED", 500),
    ],
)
def test_safe_storage_and_unexpected_errors(enabled, monkeypatch, error, code, status):
    client, _, body = enabled
    if isinstance(error, sqlite3.Error):

        def fail(*args, **kwargs):
            raise error

        monkeypatch.setattr(sqlite3, "connect", fail)
    else:

        class Broken:
            def load_native_match_records(self, *args):
                raise error

        app.dependency_overrides[get_read_only_repository] = Broken
    safe(client.post(BASE, json=body), status, code)
