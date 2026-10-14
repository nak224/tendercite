import json
import os
import subprocess
import sys
from pathlib import Path

from tendercite.cli.capability_mappings import main


def test_executable_example_and_explicit_reapproval_revocation(tmp_path, capsys):
    directory = tmp_path / "synthetic-demo"
    env = dict(os.environ, HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1")
    env.pop("TENDERCITE_LLM_API_KEY", None)
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [
            sys.executable,
            str(root / "examples/persistent_capability_mappings.py"),
            "--directory",
            str(directory),
        ],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0 and result.stderr == ""
    output = json.loads(result.stdout)
    assert output["current_status"] == "STALE"
    assert output["before_approval"]["findings"][0]["coverage"] == "NOT_EVALUATED"
    assert output["after_approval"]["findings"][0]["coverage"] == "ALL_REQUIRED_SKILLS_RECORDED"
    assert (
        "PERSISTED_MAPPING_STALE"
        in output["after_changed_review"]["findings"][0]["non_evaluation_reasons"]
    )
    assert output["history_statuses"] == ["DRAFT", "APPROVED"]
    assert "person_id" not in result.stdout and "workforce_members" not in result.stdout
    command = ["--database", str(directory / "synthetic.db")]
    for operation, name, status in [
        ("revise", "3-revision.json", "DRAFT"),
        ("approve", "4-reapproval.json", "APPROVED"),
        ("match", "2-match.json", "MATCHED"),
        ("revoke", "5-revocation.json", "REVOKED"),
    ]:
        assert main(command + [operation, "--input", str(directory / name)]) == 0
        output = json.loads(capsys.readouterr().out)
        assert output["status"] == status
        if operation == "match":
            assert output["findings"][0]["coverage"] == "ALL_REQUIRED_SKILLS_RECORDED"
            assert output["qualification"] == "NOT_VERIFIED"
    rerun = subprocess.run(
        [
            sys.executable,
            str(root / "examples/persistent_capability_mappings.py"),
            "--directory",
            str(directory),
        ],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert rerun.returncode != 0
