import json
import os
import subprocess
import sys
from pathlib import Path

from capability_helpers import CONTENT_HASH


def test_documented_synthetic_example_executes_without_database_or_credentials(tmp_path):
    root = Path(__file__).parents[1]
    env = dict(os.environ)
    env.update({"TENDERCITE_DATA_DIR": str(tmp_path / "never-created"), "HF_HUB_OFFLINE": "1"})
    env.pop("TENDERCITE_LLM_API_KEY", None)
    command = subprocess.run(
        [sys.executable, str(root / "examples/capability_matching.py")],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert command.returncode == 0 and command.stderr == ""
    result = json.loads(command.stdout)
    assert result["snapshot_hash"] == CONTENT_HASH
    assert result["individual_coverage"] == [
        "ALL_REQUIRED_SKILLS_RECORDED",
        "ALL_REQUIRED_SKILLS_RECORDED",
        "NO_REQUIRED_SKILLS_RECORDED",
    ]
    assert result["qualification"] == "NOT_VERIFIED"
    assert result["capacity_evaluation"] == "NOT_EVALUATED"
    assert not (tmp_path / "never-created").exists()
