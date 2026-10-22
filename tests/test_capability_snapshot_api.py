import asyncio
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock

import httpx
import pytest
from capability_helpers import CONTENT_HASH, FIXTURE, capacity, encode, example
from curated_intake_helpers import intake, upload
from fastapi import HTTPException
from fastapi.testclient import TestClient

from tendercite.api.dependencies import get_repository
from tendercite.api.routes.capability_snapshots import ACKNOWLEDGEMENT_HEADER
from tendercite.core.config import Settings, settings
from tendercite.main import app
from tendercite.repositories.sqlite import SqliteRepository
from tendercite.services import capabilities

BASE = "/api/v1/capability-snapshots"
JSON_HEADERS = {"Content-Type": "application/json"}
IMPORT_HEADERS = JSON_HEADERS | {ACKNOWLEDGEMENT_HEADER: "true"}


@pytest.fixture(autouse=True)
def local_only(monkeypatch):
    def forbidden(*_args, **_kwargs):
        pytest.fail("Snapshot API attempted external networking")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", forbidden)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", forbidden)


@pytest.fixture
def enabled(client, monkeypatch):
    monkeypatch.setattr(settings, "capability_snapshot_api_enabled", True)
    return client


def post(client, operation="validate", raw=None, headers=None):
    return client.post(
        f"{BASE}/{operation}",
        content=FIXTURE.read_bytes() if raw is None else raw,
        headers=headers
        if headers is not None
        else IMPORT_HEADERS
        if operation == "import"
        else JSON_HEADERS,
    )


def repo():
    return app.dependency_overrides[get_repository]()


def assert_safe(reply, status, code):
    assert reply.status_code == status
    assert reply.json() == {"detail": {"code": code}}
    assert reply.headers["cache-control"] == "no-store"
    assert "PRIVATE" not in reply.text
    assert "person_id" not in reply.text and "workforce_members" not in reply.text


@pytest.mark.parametrize("operation", ["validate", "import"])
def test_default_disabled_without_reading_body_or_database(tmp_path, monkeypatch, operation):
    monkeypatch.delenv("TENDERCITE_CAPABILITY_SNAPSHOT_API_ENABLED", raising=False)
    assert Settings(_env_file=None).capability_snapshot_api_enabled is False
    monkeypatch.setattr(settings, "capability_snapshot_api_enabled", False)
    monkeypatch.setattr(settings, "data_dir", tmp_path / "absent")
    app.dependency_overrides[get_repository] = lambda: pytest.fail("Disabled route touched DB")
    try:
        with TestClient(app) as client:
            assert_safe(
                post(client, operation, raw=b"PRIVATE"), 404, "CAPABILITY_SNAPSHOT_API_DISABLED"
            )
    finally:
        app.dependency_overrides.clear()
    assert not (tmp_path / "absent").exists()


def test_validation_has_no_repository_dependency_or_persistence(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "capability_snapshot_api_enabled", True)
    monkeypatch.setattr(settings, "data_dir", tmp_path / "absent")
    app.dependency_overrides[get_repository] = lambda: pytest.fail("Validation accessed repository")
    try:
        with TestClient(app) as client:
            reply = post(client)
    finally:
        app.dependency_overrides.clear()
    assert reply.status_code == 200
    assert reply.json() == {
        "status": "VALIDATED",
        "content_hash": CONTENT_HASH,
        "schema_version": "1.0",
        "source_system": "team-capacity-engine",
        "assessment_period": {"start_date": "2027-02-01", "end_date": "2027-02-07"},
        "member_count": 3,
        "assignment_count": 3,
        "catalog_count": 2,
        "imported_at": None,
    }
    assert reply.headers["cache-control"] == "no-store"
    assert not (tmp_path / "absent").exists()


def test_validation_preserves_existing_database_bytes(enabled):
    before = repo().database_path.read_bytes()
    assert post(enabled).status_code == 200
    assert repo().database_path.read_bytes() == before
    assert repo().list_capability_snapshots() == []


@pytest.mark.parametrize("ack", [None, "false", "True", "1", " true ", "true,true"])
def test_explicit_exact_import_acknowledgement(enabled, ack, monkeypatch):
    imported = Mock(side_effect=AssertionError("Should not import"))
    monkeypatch.setattr(repo(), "import_capability_snapshot", imported)
    headers = JSON_HEADERS | ({ACKNOWLEDGEMENT_HEADER: ack} if ack is not None else {})
    assert_safe(post(enabled, "import", headers=headers), 422, "ACKNOWLEDGEMENT_REQUIRED")
    imported.assert_not_called()
    assert repo().list_capability_snapshots() == []


def test_import_idempotency_original_metadata_restart_and_other_tables_unchanged(enabled):
    repository = repo()
    run = repository.commit_curated_intake(intake(upload(enabled)))
    finding = repository.list_findings(run.analysis_id)[0]
    assert (
        enabled.post(
            f"/api/v1/findings/{finding.id}/reviews",
            json={"action": "CONFIRM", "comment": "Synthetic review simulation"},
        ).status_code
        == 201
    )
    assert (
        enabled.patch(
            f"/api/v1/go-no-go/{finding.id}",
            json={"status": "PARTIAL", "rationale": "Synthetic operator judgment"},
        ).status_code
        == 200
    )
    with repository._connect() as connection:
        tables = [
            r[0]
            for r in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
            if r[0] != "capability_snapshots"
        ]
        before = {t: connection.execute(f'SELECT * FROM "{t}"').fetchall() for t in tables}
    first = post(enabled, "import")
    repeated = post(enabled, "import")
    assert first.status_code == repeated.status_code == 200
    assert first.json()["status"] == "INSERTED"
    assert repeated.json() == first.json() | {"status": "ALREADY_PRESENT"}
    assert first.json()["imported_at"] is not None
    assert (
        first.json()["member_count"],
        first.json()["assignment_count"],
        first.json()["catalog_count"],
    ) == (3, 3, 2)
    assert (
        SqliteRepository(repository.database_path, read_only=True).get_capability_snapshot(
            CONTENT_HASH
        )
        is not None
    )
    with repository._connect() as connection:
        assert {t: connection.execute(f'SELECT * FROM "{t}"').fetchall() for t in tables} == before
    for private in (
        "person_id",
        "workforce_members",
        "skill_catalog",
        str(repository.database_path),
    ):
        assert private not in first.text


@pytest.mark.parametrize("operation", ["validate", "import"])
@pytest.mark.parametrize(
    "failure,code",
    [
        ("hash", "HASH_MISMATCH"),
        ("reference", "INVALID_CONTRACT"),
        ("capacity", "INVALID_CONTRACT"),
        ("order", "INVALID_CONTRACT"),
        ("extra", "INVALID_CONTRACT"),
        ("version", "UNSUPPORTED_SCHEMA"),
        ("path", "INVALID_CONTRACT"),
        ("url", "INVALID_CONTRACT"),
    ],
)
def test_invalid_contract_reuses_strict_validator_and_does_not_write(
    enabled, operation, failure, code
):
    body = example()
    if failure == "hash":
        body["content_hash"] = "sha256:" + "0" * 64
    if failure == "reference":
        body["workforce_members"][0]["skills"][0]["skill_id"] = (
            "00000000-0000-0000-0000-000000000099"
        )
    if failure == "capacity":
        capacity(body)["capacity_state"] = "unknown"
    if failure == "order":
        body["workforce_members"].reverse()
    if failure == "extra":
        body["PRIVATE"] = "PRIVATE"
    if failure == "version":
        body["schema_version"] = "PRIVATE"
    if failure == "path":
        body = {"input": "/PRIVATE/data.json"}
    if failure == "url":
        body = {"url": "https://PRIVATE.invalid/snapshot.json"}
    raw = encode(body, rehash=failure != "hash")
    before = repo().database_path.read_bytes()
    assert_safe(post(enabled, operation, raw), 422, code)
    assert repo().database_path.read_bytes() == before


@pytest.mark.parametrize("operation", ["validate", "import"])
@pytest.mark.parametrize(
    "raw",
    [
        b'{"PRIVATE',
        b'{"schema_version":"1.0","schema_version":"1.0"}',
        b'{"nested":{"PRIVATE":1,"PRIVATE":2}}',
        b"NaN",
        b"\xff",
        b"[]",
    ],
)
def test_duplicate_keys_and_malformed_json_are_safe(enabled, operation, raw):
    code = "INVALID_CONTRACT" if raw == b"[]" else "INVALID_JSON"
    assert_safe(post(enabled, operation, raw), 422, code)
    assert repo().list_capability_snapshots() == []


@pytest.mark.parametrize("operation", ["validate", "import"])
@pytest.mark.parametrize("length", [None, "1"])
def test_size_boundary_not_bypassed_by_declared_length(enabled, operation, length):
    headers = IMPORT_HEADERS | ({"Content-Length": length} if length is not None else {})
    assert_safe(
        post(enabled, operation, b" " * (capabilities.MAX_SNAPSHOT_BYTES + 1), headers),
        413,
        "INPUT_TOO_LARGE",
    )


@pytest.mark.parametrize(
    "headers,status,code",
    [
        ({"Content-Type": "text/plain"}, 415, "UNSUPPORTED_MEDIA_TYPE"),
        (
            {"Content-Type": "application/json", "Content-Encoding": "gzip"},
            415,
            "UNSUPPORTED_CONTENT_ENCODING",
        ),
        (JSON_HEADERS | {"Content-Length": "PRIVATE"}, 400, "INVALID_CONTENT_LENGTH"),
        (JSON_HEADERS | {"Content-Length": "-1"}, 400, "INVALID_CONTENT_LENGTH"),
        (JSON_HEADERS | {"Content-Length": "99999999999999999999"}, 413, "INPUT_TOO_LARGE"),
        (JSON_HEADERS | {"Content-Length": "1"}, 400, "INVALID_CONTENT_LENGTH"),
    ],
)
def test_transport_errors_are_fixed(enabled, headers, status, code):
    assert_safe(post(enabled, headers=headers), status, code)


@pytest.mark.parametrize(
    "header,values,status,code",
    [
        ("Content-Type", ["application/json", "text/plain"], 415, "UNSUPPORTED_MEDIA_TYPE"),
        ("Content-Encoding", ["identity", "gzip"], 415, "UNSUPPORTED_CONTENT_ENCODING"),
        ("Content-Length", ["1", "1"], 400, "INVALID_CONTENT_LENGTH"),
        (ACKNOWLEDGEMENT_HEADER, ["true", "true"], 422, "ACKNOWLEDGEMENT_REQUIRED"),
    ],
)
def test_ambiguous_duplicate_transport_headers_are_rejected(enabled, header, values, status, code):
    headers = [(k, v) for k, v in IMPORT_HEADERS.items() if k.lower() != header.lower()]
    headers.extend((header, value) for value in values)
    assert_safe(
        enabled.post(f"{BASE}/import", content=FIXTURE.read_bytes(), headers=headers), status, code
    )


def test_exact_byte_boundary_is_accepted_and_no_content_length_stream_is_bounded(enabled):
    raw = FIXTURE.read_bytes()
    padded = raw + b" " * (capabilities.MAX_SNAPSHOT_BYTES - len(raw))
    assert post(enabled, raw=padded).status_code == 200
    response = enabled.post(
        f"{BASE}/validate",
        content=iter([b" " * capabilities.MAX_SNAPSHOT_BYTES, b" "]),
        headers=JSON_HEADERS,
    )
    assert_safe(response, 413, "INPUT_TOO_LARGE")


def test_actual_chunked_asgi_read_stops_when_limit_is_crossed(enabled):
    messages = iter(
        [
            {
                "type": "http.request",
                "body": b" " * capabilities.MAX_SNAPSHOT_BYTES,
                "more_body": True,
            },
            {"type": "http.request", "body": b" ", "more_body": True},
        ]
    )
    sent = []

    async def receive():
        try:
            return next(messages)
        except StopIteration:
            pytest.fail("Consumed body after crossing size boundary")

    async def send(message):
        sent.append(message)

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": f"{BASE}/validate",
        "query_string": b"",
        "headers": [(b"content-type", b"application/json")],
        "server": ("testserver", 80),
        "client": ("testclient", 123),
    }
    asyncio.run(app(scope, receive, send))
    assert sent[0]["status"] == 413
    assert json.loads(sent[1]["body"]) == {"detail": {"code": "INPUT_TOO_LARGE"}}


@pytest.mark.parametrize(
    "failure,status,code",
    [
        (sqlite3.OperationalError("PRIVATE"), 503, "DATABASE_FAILED"),
        (RuntimeError("PRIVATE"), 500, "CAPABILITY_SNAPSHOT_FAILED"),
        (HTTPException(500, "PRIVATE"), 500, "CAPABILITY_SNAPSHOT_FAILED"),
    ],
)
def test_database_and_unexpected_errors_are_safe(enabled, monkeypatch, failure, status, code):
    monkeypatch.setattr(repo(), "import_capability_snapshot", Mock(side_effect=failure))
    assert_safe(post(enabled, "import"), status, code)


def test_storage_integrity_conflict_preserves_original_record(enabled):
    assert post(enabled, "import").status_code == 200
    repository = repo()
    with repository._connect() as connection:
        connection.execute("UPDATE capability_snapshots SET canonical_json='{}'")
    before = repository.database_path.read_bytes()
    assert_safe(post(enabled, "import"), 409, "STORAGE_INTEGRITY")
    assert repository.database_path.read_bytes() == before


def test_concurrent_http_imports_preserve_one_identity_and_timestamp(enabled):
    def importing(_index):
        return post(enabled, "import").json()

    with ThreadPoolExecutor(max_workers=4) as workers:
        results = list(workers.map(importing, range(8)))
    assert [r["status"] for r in results].count("INSERTED") == 1
    assert {r["status"] for r in results} == {"INSERTED", "ALREADY_PRESENT"}
    assert len({r["imported_at"] for r in results}) == 1
    assert len(repo().list_capability_snapshots()) == 1


def test_openapi_describes_raw_contract_and_acknowledgement_header():
    document = app.openapi()
    path = document["paths"][f"{BASE}/import"]["post"]
    assert "CapabilitySnapshot" in str(path["requestBody"])
    assert any(
        p["name"] == ACKNOWLEDGEMENT_HEADER and p["in"] == "header" and p["required"]
        for p in path["parameters"]
    )
    assert "SnapshotResult" in str(path["responses"]["200"])
