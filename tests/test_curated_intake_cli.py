import json
import os
import socket
import subprocess
from pathlib import Path

import httpx
import pytest
from curated_intake_helpers import intake, repository, upload

from tendercite.cli.curated_intake import main
from tendercite.domain.curated_intake import MAX_INTAKE_BYTES
from tendercite.repositories.sqlite import SqliteRepository
from tendercite.services.capabilities import write_private_json


def invoke(repo, command, *args):
    return main(["--database", str(repo.database_path), command, *map(str, args)])


def test_validate_commit_inspect_are_private_without_initialization(
    client, tmp_path, monkeypatch, capsys
):
    document = upload(client)
    repo = repository(client)
    path = tmp_path / "intake.json"
    write_private_json(path, intake(document))
    before = repo.database_path.read_bytes()

    def forbidden(*_args, **_kwargs):
        pytest.fail("CLI initialized a database or attempted networking/model calls")

    from tendercite.services.llm.openai_compatible import OpenAICompatibleLLM
    from tendercite.services.retrieval.embeddings import SentenceTransformerEmbeddingProvider

    monkeypatch.setattr(SqliteRepository, "_initialize", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", forbidden)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", forbidden)
    monkeypatch.setattr(OpenAICompatibleLLM, "request_completion", forbidden)
    monkeypatch.setattr(SentenceTransformerEmbeddingProvider, "__init__", forbidden)
    assert invoke(repo, "validate", "--input", path) == 0
    validation = capsys.readouterr()
    assert json.loads(validation.out)["status"] == "VALIDATED"
    assert repo.database_path.read_bytes() == before
    for expected in ("CREATED", "ALREADY_PRESENT"):
        assert invoke(repo, "commit", "--input", path, "--confirm-manual-intake") == 0
        console = capsys.readouterr()
        output = json.loads(console.out)
        assert output["status"] == expected
        assert "PostgreSQL" not in console.out and "quote" not in console.out
        assert "synthetic_operator" not in console.out
    export = tmp_path / "original-intake-analysis.json"
    before = repo.database_path.read_bytes()
    assert invoke(repo, "inspect", "--analysis-id", output["analysis_id"], "--output", export) == 0
    assert "PostgreSQL" not in capsys.readouterr().out
    assert repo.database_path.read_bytes() == before
    body = json.loads(export.read_text())
    assert "PostgreSQL" in body["findings"][0]["statement"]
    assert body["findings"][0]["review_status"] == "UNREVIEWED"
    if os.name == "posix":
        assert export.stat().st_mode & 0o777 == 0o600


def test_no_confirmation_means_no_input_read_or_database_created(tmp_path, capsys):
    database = tmp_path / "missing.db"
    assert (
        main(["--database", str(database), "commit", "--input", str(tmp_path / "missing.json")])
        == 2
    )
    assert json.loads(capsys.readouterr().err)["error"] == "MANUAL_INTAKE_CONFIRMATION_REQUIRED"
    assert not database.exists()


@pytest.mark.parametrize("command", ["validate", "commit", "inspect"])
def test_missing_database_is_not_initialized(client, tmp_path, capsys, command):
    document = upload(client)
    source = tmp_path / "intake.json"
    write_private_json(source, intake(document))
    database = tmp_path / "missing.db"
    args = ["--analysis-id", "missing"] if command == "inspect" else ["--input", str(source)]
    if command == "commit":
        args.append("--confirm-manual-intake")
    assert main(["--database", str(database), command, *args]) == 2
    assert not database.exists() and json.loads(capsys.readouterr().err)["status"] == "ERROR"


@pytest.mark.parametrize(
    "raw, error",
    [
        (b'{"schema_version":"x","schema_version":"y"}', "INVALID_JSON"),
        (
            b'{"findings":[{"quote":"private-synthetic-marker","quote":"duplicate"}]}',
            "INVALID_JSON",
        ),
        (b'{"quote":NaN}', "INVALID_JSON"),
        (b"{", "INVALID_CONTRACT"),
        (b"{}", "INVALID_CURATED_INTAKE"),
    ],
)
def test_malformed_json_is_fixed_and_never_echoed(client, tmp_path, capsys, raw, error):
    repo = repository(client)
    source = tmp_path / "intake.json"
    source.write_bytes(raw)
    assert invoke(repo, "validate", "--input", source) == 2
    console = capsys.readouterr()
    # Malformed JSON and schema errors are both fixed, source-free categories.
    expected = "INVALID_CURATED_INTAKE" if error == "INVALID_CONTRACT" else error
    assert json.loads(console.err)["error"] == expected
    assert "private-synthetic-marker" not in console.err and console.out == ""


def test_oversized_input_is_rejected_before_parsing(client, tmp_path, capsys):
    source = tmp_path / "oversized.json"
    source.write_bytes(b" " * (MAX_INTAKE_BYTES + 1))
    assert invoke(repository(client), "validate", "--input", source) == 2
    assert json.loads(capsys.readouterr().err)["error"] == "INPUT_TOO_LARGE"


@pytest.mark.parametrize(
    "source",
    [
        "https://example.invalid/intake.json",
        "../input.json",
        "file:///tmp/input.json",
        "//remote/intake.json",
    ],
)
def test_unsafe_input_paths_are_rejected(client, capsys, source):
    assert invoke(repository(client), "validate", "--input", source) == 2
    assert json.loads(capsys.readouterr().err)["error"] == "UNSAFE_INPUT"


@pytest.mark.skipif(os.name != "posix", reason="POSIX symlink fixtures")
@pytest.mark.parametrize("kind", ["input", "parent", "database"])
def test_symlinks_are_not_followed(client, tmp_path, capsys, kind):
    document = upload(client)
    repo = repository(client)
    source = tmp_path / "input.json"
    write_private_json(source, intake(document))
    if kind == "input":
        link = tmp_path / "linked.json"
        link.symlink_to(source)
        source = link
    elif kind == "parent":
        link = tmp_path / "linked-directory"
        link.symlink_to(tmp_path, target_is_directory=True)
        source = link / "input.json"
    else:
        link = tmp_path / "linked.db"
        link.symlink_to(repo.database_path)
        repo.database_path = link
    assert invoke(repo, "validate", "--input", source) == 2
    assert json.loads(capsys.readouterr().err)["error"] == "UNSAFE_INPUT"


def test_existing_inspection_output_is_never_overwritten(client, tmp_path, capsys):
    document = upload(client)
    repo = repository(client)
    result = repo.commit_curated_intake(intake(document))
    output = tmp_path / "existing.json"
    output.write_bytes(b"existing private export")
    assert invoke(repo, "inspect", "--analysis-id", result.analysis_id, "--output", output) == 2
    assert json.loads(capsys.readouterr().err)["error"] == "OUTPUT_FAILED"
    assert output.read_bytes() == b"existing private export"


def test_data_directory_settings_ignore_provider_configuration(
    client, tmp_path, monkeypatch, capsys
):
    document = upload(client)
    repo = repository(client)
    repo.database_path.rename(tmp_path / "tendercite.db")
    monkeypatch.setenv("TENDERCITE_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("TENDERCITE_LLM_BASE_URL", "not-a-provider-url")
    source = tmp_path / "input.json"
    write_private_json(source, intake(document))
    assert main(["validate", "--input", str(source)]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "VALIDATED"


def test_default_curated_artifacts_are_excluded_from_git_and_docker(client, capsys):
    root = Path(__file__).absolute().parents[1]
    result = subprocess.run(
        ["git", "check-ignore", "data/curated/intake.json", "data/curated/inspection.json"],
        cwd=root,
        text=True,
        capture_output=True,
        check=True,
    )
    assert len(result.stdout.splitlines()) == 2
    assert "data" in (root / ".dockerignore").read_text().splitlines()
    assert invoke(repository(client), "validate", "--input", root / "intake.json") == 2
    assert json.loads(capsys.readouterr().err)["error"] == "UNSAFE_INPUT"
