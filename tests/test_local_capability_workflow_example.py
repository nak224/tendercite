import json
import os
import subprocess
import sys
from pathlib import Path

from tendercite.cli.capability_mappings import main as mapping_cli
from tendercite.cli.capability_workflow import main as workflow_cli


def test_executable_local_workflow_and_explicit_reapproval(tmp_path, capsys):
    directory = tmp_path / "workflow-example"
    root = Path(__file__).absolute().parents[1]
    environment = dict(os.environ, HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1")
    environment.pop("TENDERCITE_LLM_API_KEY", None)
    result = subprocess.run(
        [sys.executable, "-m", "examples.local_capability_workflow", "--directory", str(directory)],
        cwd=root,
        env=environment,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0 and result.stderr == ""
    summary = json.loads(result.stdout)
    assert summary["simulation_only"]
    assert summary["approved"]["current_approved"] == 1
    assert summary["after_changed_review"]["stale"] == 1
    assert summary["history_statuses"] == ["DRAFT", "APPROVED"]
    assert "PostgreSQL" not in result.stdout and "person_id" not in result.stdout
    draft = json.loads((directory / "draft-assessment.json").read_text())
    approved = json.loads((directory / "approved-assessment.json").read_text())
    stale = json.loads((directory / "stale-assessment.json").read_text())
    assert draft["findings"][0]["coverage"] == "NOT_EVALUATED"
    assert approved["findings"][0]["individual_counts"] == dict(
        all_required_recorded=2, some_required_recorded=0, no_required_recorded=1
    )
    assert stale["findings"][0]["coverage"] == "NOT_EVALUATED"
    assert stale["findings"][0]["finding"]["mapping"]["status"] == "STALE"
    assert stale["findings"][0]["qualification"] == "NOT_VERIFIED"
    assert stale["findings"][0]["capacity_evaluation"] == "NOT_EVALUATED"
    base = ["--database", str(directory / "synthetic.db")]
    for operation, name, status in (
        ("revise", "revision.json", "DRAFT"),
        ("approve", "reapproval.json", "APPROVED"),
        ("revoke", "revocation.json", "REVOKED"),
    ):
        assert mapping_cli(base + [operation, "--input", str(directory / name)]) == 0
        assert json.loads(capsys.readouterr().out)["status"] == status
    assert (
        workflow_cli(
            base
            + [
                "report",
                "--input",
                str(directory / "assessment-selection.json"),
                "--output",
                str(directory / "revoked-assessment.json"),
            ]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out)["summary"]["revoked"] == 1
    if os.name == "posix":
        assert all(path.stat().st_mode & 0o777 == 0o600 for path in directory.glob("*.json"))


def test_workflow_example_refuses_existing_database(tmp_path):
    from examples.local_capability_workflow import run_demo

    database = tmp_path / "synthetic.db"
    database.write_bytes(b"existing synthetic data")
    import pytest

    with pytest.raises(ValueError, match="fresh demo directory"):
        run_demo(tmp_path)
    assert database.read_bytes() == b"existing synthetic data"
