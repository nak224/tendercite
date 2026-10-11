"""CLI dry-run safeguards with generated PDFs and artificial pricing only."""

import json
from pathlib import Path

import httpx
import pytest
from test_cloud_llm_contexts import comparison  # noqa: F401
from test_cloud_llm_preflight import configuration
from test_external_document_metrics import corpus  # noqa: F401
from test_external_qa_spans import context  # noqa: F401
from test_external_source_span_runner import inputs  # noqa: F401

from evaluation import cloud_llm_comparison as cli
from evaluation import external_benchmark as acquisition


@pytest.fixture
def arguments(inputs, comparison, monkeypatch):  # noqa: F811 - shared generated PDF fixtures.
    for variable in (
        "TENDERCITE_LLM_BASE_URL",
        "TENDERCITE_LLM_MODEL",
        "TENDERCITE_LLM_TEMPERATURE",
        "TENDERCITE_LLM_MAX_OUTPUT_TOKENS",
        "TENDERCITE_LLM_REQUEST_TIMEOUT_SECONDS",
    ):
        monkeypatch.delenv(variable, raising=False)
    monkeypatch.setattr(cli, "load_comparison_corpus", lambda *a: comparison)
    monkeypatch.setattr(
        httpx, "AsyncClient", lambda *a, **k: pytest.fail("Paid/network call attempted")
    )
    monkeypatch.setattr(
        acquisition, "HuggingFaceSource", lambda *a: pytest.fail("Download attempted")
    )
    root = inputs["review"].parent
    config_path = root / "cloud-config.json"
    config_path.write_text(configuration().model_dump_json())
    return [
        "--config",
        str(config_path),
        "--acquisition-dir",
        str(inputs["acquisition"]),
        "--retrieval-results",
        str(inputs["retrieval"]),
        "--output-dir",
        str(root / "dry-run"),
    ]


def test_default_is_dry_run_and_credentials_are_not_sent_or_logged(arguments, monkeypatch, capsys):
    monkeypatch.setenv("TENDERCITE_LLM_API_KEY", "private-unit-test-key-marker")
    assert cli.main(arguments) == 0
    output = Path(arguments[-1])
    report = json.loads((output / "preflight.json").read_text())
    assert report["status"] == "READY_DRY_RUN" and report["api_calls"] == 0
    assert report["credential_environment_present"] and not report["api_execution_available"]
    assert "explicit_execution_opt_in_required" in report["execution_blockers"]
    assert "private-unit-test-key-marker" not in capsys.readouterr().out
    assert all("private-unit-test-key-marker" not in file.read_text() for file in output.iterdir())
    original = (output / "preflight.json").read_bytes()
    assert cli.main(arguments) == 1
    assert (output / "preflight.json").read_bytes() == original


def test_dry_run_does_not_require_credentials(arguments, monkeypatch):
    monkeypatch.delenv("TENDERCITE_LLM_API_KEY", raising=False)
    assert cli.main(arguments + ["--dry-run"]) == 0
    record = json.loads((Path(arguments[-1]) / "preflight.json").read_text())
    assert not record["credential_environment_present"]
    assert "api_key_environment_variable_missing" in record["execution_blockers"]


def test_execute_requires_an_explicit_question_even_with_key_and_permission(arguments, monkeypatch):
    monkeypatch.setenv("TENDERCITE_LLM_API_KEY", "test-only-key")
    assert cli.main(arguments + ["--execute", "--confirm-transmission"]) == 1
    assert not Path(arguments[-1]).exists()


def test_confirmation_is_provider_and_artifact_specific_without_sending(arguments):
    assert cli.main(arguments + ["--confirm-transmission"]) == 0
    record = json.loads((Path(arguments[-1]) / "preflight.json").read_text())
    permission = record["transmission_permission_declaration"]
    assert permission["confirmed"]
    assert permission["provider_endpoint"] == "https://provider.invalid/v1"
    assert permission["input_provenance"] == record["provenance"]
    assert record["api_calls"] == 0


@pytest.mark.parametrize(
    "failure", ["missing_config", "missing_pricing", "context", "budget", "embedded_key"]
)
def test_failures_are_clear_and_no_paid_calls_or_key_leaks_occur(arguments, failure):
    path = Path(arguments[1])
    value = json.loads(path.read_text())
    if failure == "missing_config":
        path.unlink()
    elif failure == "missing_pricing":
        value.pop("pricing")
    elif failure == "context":
        value["usable_context_tokens"] = 1
    elif failure == "budget":
        value["spending"].update(per_request="0.000001", cumulative="0.000002")
    else:
        value["api_key"] = "must-never-log-unit-test-key"
    if failure != "missing_config":
        path.write_text(json.dumps(value))
    assert cli.main(arguments) == 1
    text = (Path(arguments[-1]) / "preflight.json").read_text()
    report = json.loads(text)
    assert report["status"] in {"BLOCKED_INPUT", "BLOCKED_PREFLIGHT"}
    assert report["api_calls"] == 0 and not report["api_execution_available"]
    assert "must-never-log-unit-test-key" not in text


def test_ignored_local_storage_is_enforced(arguments, tmp_path):
    arguments[-1] = str(tmp_path.parent / "do-not-create-cloud-prompts")
    assert cli.main(arguments) == 1
    assert not Path(arguments[-1]).exists()


def test_placeholder_configuration_rejects_unverified_prices_and_context():
    from evaluation.cloud_llm_preflight import ComparisonConfig

    example = Path(__file__).parents[1] / "evaluation/cloud-llm-config.example.json"
    with pytest.raises(ValueError):
        ComparisonConfig.model_validate_json(example.read_text())


def test_failed_prompt_persistence_cannot_be_reported_as_ready(arguments, monkeypatch):
    original = cli.write_new_json

    def fail_prompts(path, value):
        if path.name == "prompts.json":
            raise OSError("Synthetic output failure")
        return original(path, value)

    monkeypatch.setattr(cli, "write_new_json", fail_prompts)
    assert cli.main(arguments) == 1
    record = json.loads((Path(arguments[-1]) / "preflight.json").read_text())
    assert record["status"] == "BLOCKED_INPUT" and record["failure"] == "Synthetic output failure"
