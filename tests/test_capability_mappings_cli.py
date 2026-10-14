import json
import os
import socket
import subprocess
import sys

import httpx
import pytest
from matching_helpers import snapshot
from persistent_mapping_helpers import (
    approval_input,
    draft_input,
    revision_input,
    revocation_input,
    seeded_repository,
)

from tendercite.cli import capability_mappings as cli
from tendercite.domain.capability_matching import CapabilityMatchingRequest


@pytest.fixture
def repo(tmp_path, monkeypatch):
    monkeypatch.setenv("TENDERCITE_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("TENDERCITE_LLM_BASE_URL", "invalid-secret-provider-configuration")
    monkeypatch.delenv("TENDERCITE_LLM_API_KEY", raising=False)

    def forbidden(*_a, **_kw):
        pytest.fail("Mapping CLI attempted network access")

    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", forbidden)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", forbidden)
    return seeded_repository(tmp_path / "tendercite.db")


def call_json(capsys, args):
    assert cli.main(args) == 0
    output = capsys.readouterr()
    assert output.err == ""
    assert "person_id" not in output.out and "remaining_hours" not in output.out
    return json.loads(output.out)


def write_input(tmp_path, request):
    path = tmp_path / "operation.json"
    path.write_text(request.model_dump_json(), encoding="utf-8")
    return str(path)


def test_full_explicit_cli_workflow_and_no_personnel_output(repo, tmp_path, capsys):
    identity = [
        "--analysis-id",
        "analysis-1",
        "--finding-id",
        "finding-1",
        "--snapshot-hash",
        snapshot().content_hash,
    ]
    assert call_json(capsys, ["review-state", *identity])["review_binding"]["review_event_id"]
    first = call_json(capsys, ["create", "--input", write_input(tmp_path, draft_input(repo))])
    mapping_id = first["mapping"]["mapping_id"]
    draft = repo.get_capability_mapping(mapping_id).record
    selection = CapabilityMatchingRequest(
        analysis_id="analysis-1",
        snapshot_hash=snapshot().content_hash,
        finding_ids=("finding-1",),
        mappings=(),
    )

    def run_match():
        return call_json(capsys, ["match", "--input", write_input(tmp_path, selection)])

    assert run_match()["findings"][0]["coverage"] == "NOT_EVALUATED"
    approved = call_json(
        capsys, ["approve", "--input", write_input(tmp_path, approval_input(draft))]
    )
    assert approved["mapping"]["revision"] == 2
    matched = run_match()
    assert matched["findings"][0]["individual_coverage_counts"] == {
        "ALL_REQUIRED_SKILLS_RECORDED": 2,
        "NO_REQUIRED_SKILLS_RECORDED": 1,
    }
    assert matched["qualification"] == "NOT_VERIFIED"
    assert matched["capacity_evaluation"] == "NOT_EVALUATED"
    assert (
        call_json(capsys, ["list", "--analysis-id", "analysis-1", "--limit", "1"])["mappings"][0][
            "status"
        ]
        == "APPROVED"
    )
    assert call_json(capsys, ["inspect", "--mapping-id", mapping_id])["status"] == "APPROVED"
    current = repo.get_capability_mapping(mapping_id).record
    call_json(capsys, ["revise", "--input", write_input(tmp_path, revision_input(repo, current))])
    assert run_match()["findings"][0]["coverage"] == "NOT_EVALUATED"
    current = repo.get_capability_mapping(mapping_id).record
    call_json(capsys, ["revoke", "--input", write_input(tmp_path, revocation_input(current))])
    history = call_json(capsys, ["history", "--mapping-id", mapping_id])
    assert [r["status"] for r in history["revisions"]] == ["DRAFT", "APPROVED", "DRAFT", "REVOKED"]
    assert "PERSISTED_MAPPING_REVOKED" in run_match()["findings"][0]["non_evaluation_reasons"]


@pytest.mark.parametrize(
    "args",
    [
        ["list", "--limit", "0"],
        ["list", "--limit", "101"],
        ["inspect", "--mapping-id", "private-personnel-identifier"],
        ["list", "--unknown-option", "private-personnel-identifier"],
        ["create"],
        ["approve"],
        ["revoke"],
        ["create", "--input", "https://private-personnel-identifier/input.json"],
    ],
)
def test_safe_argument_errors_do_not_echo_records_or_provider_configuration(repo, capsys, args):
    assert cli.main(args) == 2
    output = capsys.readouterr()
    assert output.out == ""
    assert json.loads(output.err)["status"] == "ERROR"
    assert "private-personnel-identifier" not in output.err
    assert "invalid-secret-provider-configuration" not in output.err


@pytest.mark.parametrize(
    "payload",
    [
        '{"schema_version":"capability-mapping-draft-1","schema_version":"future"}',
        '{"rationale":NaN}',
        '{"rationale":"private-personnel-value"}',
        "[]",
        "invalid-json",
    ],
)
def test_bad_input_is_rejected_without_creating_or_changing_records(
    repo, tmp_path, capsys, payload
):
    path = tmp_path / "invalid.json"
    path.write_text(payload)
    before = repo.database_path.read_bytes()
    assert cli.main(["create", "--input", str(path)]) == 2
    output = capsys.readouterr()
    assert "private-personnel-value" not in output.err
    assert json.loads(output.err)["error"] == "INVALID_MAPPING_INPUT"
    assert before == repo.database_path.read_bytes()


def test_oversize_symlink_and_inline_mapping_inputs_are_rejected(repo, tmp_path, capsys):
    large = tmp_path / "large.json"
    large.write_bytes(b" " * 65537)
    assert cli.main(["create", "--input", str(large)]) == 2
    assert json.loads(capsys.readouterr().err)["error"] == "INPUT_TOO_LARGE"
    linked = tmp_path / "symlink.json"
    linked.symlink_to(large)
    assert cli.main(["create", "--input", str(linked)]) == 2
    assert json.loads(capsys.readouterr().err)["error"] == "UNSAFE_INPUT"
    from matching_helpers import request

    path = write_input(tmp_path, request(snapshot()))
    assert cli.main(["match", "--input", path]) == 2
    assert json.loads(capsys.readouterr().err)["error"] == "INVALID_MAPPING_INPUT"


def test_real_module_restarts_read_same_database_without_credentials(repo, tmp_path):
    path = write_input(tmp_path, draft_input(repo))
    env = dict(os.environ)
    env.pop("TENDERCITE_LLM_API_KEY", None)
    command = [
        sys.executable,
        "-m",
        "tendercite.cli.capability_mappings",
        "--database",
        str(repo.database_path),
    ]
    result = subprocess.run(
        command + ["create", "--input", path], env=env, capture_output=True, text=True, timeout=30
    )
    assert result.returncode == 0 and result.stderr == ""
    mapping_id = json.loads(result.stdout)["mapping"]["mapping_id"]
    for args in (
        ["list"],
        ["inspect", "--mapping-id", mapping_id],
        ["history", "--mapping-id", mapping_id],
    ):
        result = subprocess.run(command + args, env=env, capture_output=True, text=True, timeout=30)
        assert result.returncode == 0 and result.stderr == ""
        assert "person_id" not in result.stdout and "workforce_members" not in result.stdout


def test_missing_database_fails_without_initializing_it(tmp_path, capsys):
    database = tmp_path / "missing.db"
    assert cli.main(["--database", str(database), "list"]) == 2
    assert json.loads(capsys.readouterr().err)["error"] == "MAPPING_NOT_FOUND"
    assert not database.exists()
