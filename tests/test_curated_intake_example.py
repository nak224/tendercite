import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


def test_executable_generated_pdf_intake_and_capability_report(tmp_path):
    root = Path(__file__).absolute().parents[1]
    directory = tmp_path / "curated-demo"
    environment = dict(os.environ, HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1")
    environment.pop("TENDERCITE_LLM_API_KEY", None)
    result = subprocess.run(
        [sys.executable, "-m", "examples.curated_tender_intake", "--directory", str(directory)],
        cwd=root,
        env=environment,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0
    output = json.loads(result.stdout)
    assert output["simulation_only"] and output["validation"] == "VALIDATED"
    assert output["commit"] == "CREATED" and output["repeated_commit"] == "ALREADY_PRESENT"
    assert output["before_review"]["not_eligible_for_matching"] == 1
    assert output["approved"]["current_approved"] == 1
    assert output["stale"]["stale"] == 1
    assert "PostgreSQL" not in result.stdout and "person_id" not in result.stdout
    original = json.loads((directory / "original-analysis.json").read_text())
    assert original["findings"][0]["review_status"] == "UNREVIEWED"
    assert original["curated_intake"]["provider_invoked"] is False
    assert original["curated_intake"]["embeddings_invoked"] is False
    assert original["findings"][0]["evidence"][0]["grounding_status"] == "VERIFIED_QUOTE"
    report = json.loads((directory / "approved-assessment.json").read_text())
    assert report["findings"][0]["individual_counts"] == dict(
        all_required_recorded=2, some_required_recorded=0, no_required_recorded=1
    )
    assert (
        report["qualification"] == "NOT_VERIFIED"
        and report["capacity_evaluation"] == "NOT_EVALUATED"
    )
    assert len(list((directory / "uploads").glob("*.pdf"))) == 1
    if os.name == "posix":
        assert all(p.stat().st_mode & 0o777 == 0o600 for p in directory.glob("*.json"))


def test_example_never_overwrites_existing_local_data(tmp_path):
    from examples.curated_tender_intake import run_demo

    retained = tmp_path / "private.json"
    retained.write_text("preserve existing data")
    with pytest.raises(ValueError, match="fresh synthetic directory"):
        run_demo(tmp_path)
    assert retained.read_text() == "preserve existing data"
