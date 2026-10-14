import json
import os
import socket
import subprocess
import sys

import httpx
import pytest
from capability_helpers import CONTENT_HASH, FIXTURE, encode, example

from tendercite.cli import import_capabilities as cli
from tendercite.repositories.sqlite import SqliteRepository
from tendercite.services import capabilities


@pytest.fixture(autouse=True)
def local_only(tmp_path, monkeypatch):
    monkeypatch.setenv("TENDERCITE_DATA_DIR", str(tmp_path / "private-data"))
    monkeypatch.delenv("TENDERCITE_LLM_API_KEY", raising=False)

    def forbidden(*_a, **_kw):
        pytest.fail("Local snapshot command attempted a network request")

    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", forbidden)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", forbidden)


def test_validation_only_does_not_initialize_database_or_access_config(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.setenv("TENDERCITE_LLM_BASE_URL", "not-a-valid-provider-url")
    monkeypatch.setattr(
        cli, "_repository", lambda **_kw: pytest.fail("Validation touched DB/config")
    )
    assert cli.main(["--validate-only", "--input", str(FIXTURE)]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "VALIDATED" and result["content_hash"] == CONTENT_HASH
    assert (result["member_count"], result["assignment_count"], result["catalog_count"]) == (
        3,
        3,
        2,
    )
    assert not (tmp_path / "private-data").exists()
    assert "workforce_members" not in result and "person_id" not in result


def test_import_requires_acknowledgement_without_creating_database(tmp_path, capsys):
    assert cli.main(["--input", str(FIXTURE)]) == 2
    assert json.loads(capsys.readouterr().err)["error"] == "ACKNOWLEDGEMENT_REQUIRED"
    assert not (tmp_path / "private-data").exists()


def test_cli_import_listing_lookup_and_explicit_private_export(tmp_path, capsys):
    args = ["--input", str(FIXTURE), "--acknowledge-local-synthetic-data"]
    assert cli.main(args) == 0
    first = json.loads(capsys.readouterr().out)
    assert first["status"] == "INSERTED"
    assert cli.main(args) == 0
    repeated = json.loads(capsys.readouterr().out)
    assert (
        repeated["status"] == "ALREADY_PRESENT" and first["imported_at"] == repeated["imported_at"]
    )
    database = tmp_path / "private-data/tendercite.db"
    original_bytes = database.read_bytes()
    assert cli.main(["--list", "--limit", "1"]) == 0
    listed = json.loads(capsys.readouterr().out)
    assert listed["snapshots"][0]["content_hash"] == CONTENT_HASH
    assert len(listed["snapshots"]) == 1
    assert cli.main(["--get", CONTENT_HASH]) == 0
    found = json.loads(capsys.readouterr().out)
    assert found["status"] == "FOUND" and "workforce_members" not in found
    output = tmp_path / "retrieved.json"
    assert cli.main(["--get", CONTENT_HASH, "--output", str(output)]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "EXPORTED"
    assert capabilities.read_capability_snapshot(output).content_hash == CONTENT_HASH
    if os.name == "posix":
        assert output.stat().st_mode & 0o777 == 0o600
    assert cli.main(["--get", CONTENT_HASH, "--output", str(output)]) == 2
    assert json.loads(capsys.readouterr().err)["error"] == "OUTPUT_FAILED"
    assert database.read_bytes() == original_bytes
    assert len(SqliteRepository(database, read_only=True).list_capability_snapshots()) == 1


@pytest.mark.parametrize("mode", ["validate", "import"])
def test_invalid_json_contract_never_creates_database_or_echoes_personnel(tmp_path, capsys, mode):
    path = tmp_path / "synthetic-private-path.json"
    body = example()
    body["workforce_members"][0]["email"] = "synthetic-sensitive@example.invalid"
    path.write_bytes(encode(body))
    args = ["--input", str(path)]
    args.append("--validate-only" if mode == "validate" else "--acknowledge-local-synthetic-data")
    assert cli.main(args) == 2
    output = capsys.readouterr()
    assert output.out == ""
    assert json.loads(output.err)["error"] == "INVALID_CONTRACT"
    assert "synthetic-sensitive" not in output.err and "synthetic-private-path" not in output.err
    assert not (tmp_path / "private-data").exists()


@pytest.mark.parametrize(
    "args",
    [
        [],
        ["--input", "personnel-secret.json", "--list"],
        ["--list", "--limit", "secret"],
        ["--list", "--limit", "101"],
        ["--list", "--validate-only"],
        ["--input", "personnel-secret.json", "--output", "secret.json"],
        ["--get", "personnel-secret"],
        ["--unexpected-personnel-value"],
    ],
)
def test_argument_errors_are_fixed_and_do_not_echo_untrusted_values(args, capsys):
    assert cli.main(args) == 2
    output = capsys.readouterr()
    assert json.loads(output.err)["error"] == "INVALID_ARGUMENTS"
    assert "secret" not in output.err and "personnel" not in output.err and output.out == ""


def test_missing_database_and_missing_snapshot_are_explicit_not_found(tmp_path, capsys):
    assert cli.main(["--list"]) == 3
    assert json.loads(capsys.readouterr().err)["error"] == "NOT_FOUND"
    assert not (tmp_path / "private-data").exists()
    SqliteRepository(tmp_path / "private-data/tendercite.db")
    assert cli.main(["--get", "sha256:" + "0" * 64]) == 3
    assert json.loads(capsys.readouterr().err)["error"] == "NOT_FOUND"


def test_configuration_failures_do_not_disclose_values_or_create_data(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.setenv("TENDERCITE_LLM_BASE_URL", "synthetic-secret-provider-value")
    assert cli.main(["--input", str(FIXTURE), "--acknowledge-local-synthetic-data"]) == 2
    output = capsys.readouterr()
    assert json.loads(output.err)["error"] == "CONFIGURATION_FAILED"
    assert "synthetic-secret-provider-value" not in output.err
    assert not (tmp_path / "private-data").exists()


def test_actual_module_commands_need_no_credentials_and_persist_across_processes(tmp_path):
    env = dict(os.environ)
    env.pop("TENDERCITE_LLM_API_KEY", None)
    command = [sys.executable, "-m", "tendercite.cli.import_capabilities"]
    for args, status in [
        (["--input", str(FIXTURE), "--validate-only"], "VALIDATED"),
        (["--input", str(FIXTURE), "--acknowledge-local-synthetic-data"], "INSERTED"),
        (["--get", CONTENT_HASH], "FOUND"),
        (["--list"], "LISTED"),
    ]:
        result = subprocess.run(command + args, env=env, capture_output=True, text=True, timeout=30)
        assert result.returncode == 0 and result.stderr == ""
        assert json.loads(result.stdout)["status"] == status
        assert "person_id" not in result.stdout and "remaining_hours" not in result.stdout


def test_invalid_import_leaves_existing_database_bytes_unchanged(tmp_path, capsys):
    database = tmp_path / "private-data/tendercite.db"
    repo = SqliteRepository(database)
    repo.import_capability_snapshot(capabilities.read_capability_snapshot(FIXTURE))
    before = database.read_bytes()
    path = tmp_path / "invalid.json"
    body = example()
    body["content_hash"] = "sha256:" + "0" * 64
    path.write_bytes(encode(body, rehash=False))
    assert cli.main(["--input", str(path), "--acknowledge-local-synthetic-data"]) == 2
    assert json.loads(capsys.readouterr().err)["error"] == "HASH_MISMATCH"
    assert database.read_bytes() == before
