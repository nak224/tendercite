"""Safe local manual pipeline HTTP operations over synthetic declarations."""

import asyncio
import json
import socket
import sqlite3

import httpx
import pytest
from fastapi import HTTPException
from opportunity_helpers import create_input as opportunity_input
from pipeline_helpers import OPPORTUNITY_ID, PIPELINE_ID, create_body, update_body

from tendercite.api.dependencies import get_repository
from tendercite.core.config import Settings, settings
from tendercite.main import app

BASE = "/api/v1/pipeline"
DETAIL = f"{BASE}/{PIPELINE_ID}"


@pytest.fixture
def enabled(client, monkeypatch):
    monkeypatch.setattr(settings, "pipeline_api_enabled", True)
    repo = app.dependency_overrides[get_repository]()
    repo.create_opportunity(opportunity_input())
    return client, repo


def safe(response, status, code):
    assert response.status_code == status
    assert response.json() == {"detail": {"code": code}}
    assert response.headers["cache-control"] == "no-store"
    assert "PRIVATE" not in response.text and "Traceback" not in response.text


def test_disabled_by_default_before_body_or_database(client, monkeypatch):
    monkeypatch.delenv("TENDERCITE_PIPELINE_API_ENABLED", raising=False)
    assert Settings(_env_file=None).pipeline_api_enabled is False
    monkeypatch.setattr(settings, "pipeline_api_enabled", False)
    app.dependency_overrides[get_repository] = lambda: pytest.fail("Database access")
    for response in (
        client.get(BASE),
        client.get(DETAIL),
        client.post(BASE, content=b"PRIVATE"),
        client.put(DETAIL, content=b"PRIVATE"),
    ):
        safe(response, 404, "PIPELINE_API_DISABLED")


def test_create_list_retrieve_update_filter_and_duplicate_no_external_actions(enabled, monkeypatch):
    client, repo = enabled

    def forbidden(*args, **kwargs):
        pytest.fail("Unexpected network or service")

    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", forbidden)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", forbidden)
    original = repo.get_opportunity(OPPORTUNITY_ID)
    assert client.get(BASE).json()["entries"] == []
    created = client.post(BASE, json=create_body())
    assert created.status_code == 201 and created.headers["cache-control"] == "no-store"
    entry = created.json()["entry"]
    assert entry["schema_version"] == "native-pipeline-entry-1"
    assert entry["record"]["values"]["stage"] == "SAVED"
    assert client.get(DETAIL).json() == entry
    for revision, stage in enumerate(
        ("WON", "SAVED", "ARCHIVED", "SUBMITTED", "LOST", "REVIEWING", "PREPARING"), 1
    ):
        response = client.put(DETAIL, json=update_body(revision, stage=stage))
        assert response.status_code == 200 and response.json()["status"] == "UPDATED"
        assert response.json()["entry"]["record"]["revision"] == revision + 1
    entry = response.json()["entry"]
    assert client.get(BASE, params={"stage": "PREPARING"}).json()["entries"] == [entry]
    assert client.get(BASE, params={"stage": "WON"}).json()["entries"] == []
    assert client.get(BASE, params={"opportunity_id": str(OPPORTUNITY_ID)}).json()["entries"] == [
        entry
    ]
    again = client.post(BASE, json=create_body())
    assert again.status_code == 200 and again.json()["status"] == "ALREADY_PRESENT"
    assert again.json()["entry"] == entry
    assert repo.get_opportunity(OPPORTUNITY_ID) == original and repo.list_findings() == []
    assert repo.list_profiles().profiles == () and repo.list_capability_snapshots() == []
    safe(client.put(DETAIL, json=update_body()), 409, "PIPELINE_REVISION_CONFLICT")
    paths = app.openapi()["paths"]
    assert "PipelineCreate" in str(paths[BASE]["post"]["requestBody"])
    assert "PipelineUpdate" in str(paths[BASE + "/{pipeline_id}"]["put"]["requestBody"])


def test_missing_references_invalid_ids_and_scope_queries(enabled):
    client, _ = enabled
    safe(client.get(DETAIL), 404, "PIPELINE_NOT_FOUND")
    safe(client.get(f"{BASE}/PRIVATE"), 422, "INVALID_PIPELINE_INPUT")
    body = create_body(opportunity_id="00000000-0000-0000-0000-000000000001")
    safe(client.post(BASE, json=body), 404, "OPPORTUNITY_NOT_FOUND")
    for params in (
        {"stage": "PRIVATE"},
        {"limit": 101},
        {"offset": -1},
        {"opportunity_id": "PRIVATE"},
        {"url": "PRIVATE"},
        [("stage", "SAVED"), ("stage", "WON")],
    ):
        safe(client.get(BASE, params=params), 422, "INVALID_PIPELINE_INPUT")


@pytest.mark.parametrize("method,path", [("post", BASE), ("put", DETAIL)])
@pytest.mark.parametrize("raw", [b'{"x":1,"x":2}', b'{"x":NaN}', b'{"PRIVATE":', b"[]", b"\xff"])
def test_strict_json_sanitized(enabled, method, path, raw):
    client, repo = enabled
    before = repo.database_path.read_bytes()
    safe(
        getattr(client, method)(path, content=raw, headers={"Content-Type": "application/json"}),
        422,
        "INVALID_PIPELINE_INPUT",
    )
    assert repo.database_path.read_bytes() == before


@pytest.mark.parametrize("method,path", [("post", BASE), ("put", DETAIL)])
def test_size_encoding_and_unexpected_fields(enabled, method, path):
    client, _ = enabled
    call = getattr(client, method)
    safe(
        call(path, content=b" " * 65537, headers={"Content-Type": "application/json"}),
        413,
        "PIPELINE_REQUEST_TOO_LARGE",
    )
    safe(
        call(path, content=b"{}", headers={"Content-Type": "text/plain"}),
        415,
        "UNSUPPORTED_MEDIA_TYPE",
    )
    safe(
        call(
            path,
            content=b"{}",
            headers={"Content-Type": "application/json", "Content-Encoding": "gzip"},
        ),
        415,
        "UNSUPPORTED_CONTENT_ENCODING",
    )
    safe(
        call(
            path,
            json={**(create_body() if method == "post" else update_body()), "PRIVATE": "PRIVATE"},
        ),
        422,
        "INVALID_PIPELINE_INPUT",
    )


def test_stream_without_content_length_bounded_before_dependency(enabled):
    app.dependency_overrides[get_repository] = lambda: pytest.fail("DB access")
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
    assert json.loads(sent[1]["body"])["detail"]["code"] == "PIPELINE_REQUEST_TOO_LARGE"


@pytest.mark.parametrize(
    "error,code,status",
    [
        (sqlite3.OperationalError("PRIVATE"), "DATABASE_FAILED", 503),
        (RuntimeError("PRIVATE"), "PIPELINE_OPERATION_FAILED", 500),
        (HTTPException(500, "PRIVATE"), "PIPELINE_OPERATION_FAILED", 500),
    ],
)
def test_database_and_unexpected_errors_are_private(enabled, monkeypatch, error, code, status):
    client, repo = enabled

    def fail(*args, **kwargs):
        raise error

    if isinstance(error, sqlite3.Error):
        monkeypatch.setattr(sqlite3, "connect", fail)
    else:
        monkeypatch.setattr(repo, "list_pipeline", fail)
    safe(client.get(BASE), status, code)
