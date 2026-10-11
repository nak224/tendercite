"""Offline quota incident/retry regression; generated fixtures and mocked HTTP only."""

import copy
import hashlib
import json
from decimal import Decimal

import httpx
import pytest
from test_cloud_llm_contexts import comparison  # noqa: F401
from test_cloud_llm_execution import (  # noqa: F401 - shared synthetic fixtures.
    KEY,
    cli_arguments,
    completion,
    invoke,
    only_mock_http,
    pilot,
)
from test_external_document_metrics import corpus  # noqa: F401
from test_external_qa_spans import context  # noqa: F401
from test_external_source_span_runner import inputs  # noqa: F401

from evaluation import cloud_llm_comparison as cli
from evaluation import reconcile_cloud_llm_ledger as offline
from evaluation.cloud_llm_execution import response_record
from evaluation.cloud_llm_ledger import ExperimentLedger, atomic_save, experiment_lock, ledger_path
from evaluation.external_qa_spans import write_new_json
from tendercite.services.llm.openai_compatible import CompletionResponse

REQUEST = "req-original-synthetic-quota"
REASON = "Operator checked synthetic billing fixture and confirmed zero USD."


def quota_body():
    return {
        "error": {
            "type": "insufficient_quota",
            "code": "credit_balance_exhausted",
            "message": "Original synthetic depleted credit balance.",
        }
    }


@pytest.fixture
def legacy_incident(pilot):  # noqa: F811
    config, corpus_data, questions, _, _, output = pilot
    path = ledger_path()
    with experiment_lock(path):
        ledger = ExperimentLedger(path, config, corpus_data)
        entry = ledger.reserve(questions[0], "0.19928000", output)
        ledger.reconcile(entry, None)
        # Recreate the existing PR #15 shape, not a migration that edits its entry.
        entry.pop("attempt_id")
        entry["key"] = hashlib.sha256(f"{questions[0].question_id}:e5_rag".encode()).hexdigest()
        atomic_save(path, ledger.data)
    record = {
        "http_status": 429,
        "provider_request_id": REQUEST,
        "reservation_key": entry["key"],
        "question_id": entry["question_id"],
        "strategy": "e5_rag",
        "raw_response": json.dumps(quota_body()),
        "original_provider_error": quota_body()["error"],
        "accounting_status": "UNCERTAIN",
        "response_validity": "INVALID_OR_INCOMPLETE",
        "error": "Actual model is missing or differs from configured model",
    }
    write_new_json(output / "e5_rag.json", record)
    return entry, output / "e5_rag.json"


def confirm(entry, **changes):
    return offline.reconcile_zero(
        entry["key"],
        changes.pop("request_id", REQUEST),
        confirmed=changes.pop("confirmed", True),
        reason=REASON,
        operator_alias="operator-test",
        **changes,
    )


def test_original_429_is_classified_before_model_validation_and_never_free(pilot):  # noqa: F811
    config, corpus_data, questions, _, _, _ = pilot
    response = CompletionResponse(json.dumps(quota_body()).encode(), 429, REQUEST, 0.01)
    record, accounting = response_record(response, questions[0], corpus_data, config)
    assert record["primary_cause"] == "credit_balance_exhausted"
    assert record["provider_error_type"] == "insufficient_quota"
    assert record["provider_error_message"] == quota_body()["error"]["message"]
    assert record["provider_request_id"] == REQUEST and record["http_status"] == 429
    assert "missing" not in record["error"] and accounting is None
    assert record["accounting_status"] == "UNCERTAIN"


def test_zero_confirmation_preserves_original_entry_response_and_immutable_audit(legacy_incident):
    entry, response_path = legacy_incident
    original = copy.deepcopy(entry)
    response_bytes = response_path.read_bytes()
    result = confirm(entry)
    ledger = ExperimentLedger.existing(ledger_path())
    assert ledger.data["entries"] == [original]
    assert ledger.committed() == 0 and ledger.effective_status(entry) == "MANUALLY_RECONCILED_ZERO"
    assert result["api_calls"] == 0 and result["committed_usd"] == "0"
    pointer = ledger.data["manual_reconciliations"][0]
    audit = json.loads((ledger_path().parent / pointer["audit_file"]).read_text())
    assert audit["original_entry"]["status"] == "UNCERTAIN"
    assert audit["original_entry"]["reserved_cost"] == "0.19928000"
    assert audit["affected_attempt"] == f"legacy:{entry['key']}"
    assert audit["confirmed_billed_usd"] == "0" and audit["recorded_at"]
    assert audit["reason"] == REASON and audit["provider_request_id"] == REQUEST
    assert audit["response_file_sha256"] == hashlib.sha256(response_bytes).hexdigest()
    assert response_path.read_bytes() == response_bytes
    assert (
        pointer["audit_sha256"]
        == hashlib.sha256((ledger_path().parent / pointer["audit_file"]).read_bytes()).hexdigest()
    )


@pytest.mark.parametrize(
    "failure",
    [
        "no_confirmation",
        "wrong_request",
        "wrong_entry",
        "wrong_code",
        "wrong_type",
        "wrong_http",
        "usage",
        "wrong_question",
    ],
)
def test_unconfirmed_or_unrelated_reconciliation_is_rejected_without_changes(
    legacy_incident, failure
):
    entry, response_path = legacy_incident
    original = ledger_path().read_bytes()
    record = json.loads(response_path.read_text())
    body = json.loads(record["raw_response"])
    kwargs = {}
    if failure == "no_confirmation":
        kwargs["confirmed"] = False
    elif failure == "wrong_request":
        kwargs["request_id"] = "req-unrelated"
    elif failure == "wrong_entry":
        entry = {**entry, "key": "f" * 64}
    elif failure == "wrong_code":
        body["error"]["code"] = "rate_limit_exceeded"
    elif failure == "wrong_type":
        body["error"]["type"] = "different-error"
    elif failure == "wrong_http":
        record["http_status"] = 500
    elif failure == "usage":
        body["usage"] = {"prompt_tokens": 1, "completion_tokens": 0, "total_tokens": 1}
    else:
        record["question_id"] = "unrelated-question"
    record["raw_response"] = json.dumps(body)
    response_path.write_text(json.dumps(record))
    with pytest.raises(ValueError):
        confirm(entry, **kwargs)
    assert ledger_path().read_bytes() == original
    assert not (ledger_path().parent / "reconciliations").exists()


def test_duplicate_reconciliation_and_audit_tampering_are_rejected(legacy_incident):
    entry, _ = legacy_incident
    confirm(entry)
    before = ledger_path().read_bytes()
    ledger = ExperimentLedger.existing(ledger_path())
    audit_file = ledger_path().parent / ledger.data["manual_reconciliations"][0]["audit_file"]
    audit_bytes = audit_file.read_bytes()
    with pytest.raises(ValueError, match="already been reconciled"):
        confirm(entry)
    assert before == ledger_path().read_bytes() and audit_bytes == audit_file.read_bytes()
    audit_file.write_bytes(audit_bytes + b" ")
    with pytest.raises(ValueError, match="audit does not match"):
        ExperimentLedger.existing(ledger_path())


def test_audit_publication_failure_never_releases_reservation_and_can_recover(
    legacy_incident, monkeypatch
):
    entry, _ = legacy_incident
    original = ledger_path().read_bytes()
    publish = offline.atomic_save
    monkeypatch.setattr(
        offline, "atomic_save", lambda *a: (_ for _ in ()).throw(OSError("Synthetic disk failure"))
    )
    with pytest.raises(OSError):
        confirm(entry)
    assert original == ledger_path().read_bytes()
    assert ExperimentLedger.existing(ledger_path()).committed() == Decimal("0.19928000")
    audit_file = ledger_path().parent / "reconciliations" / f"{entry['key']}.json"
    audit_bytes = audit_file.read_bytes()
    monkeypatch.setattr(offline, "atomic_save", publish)
    confirm(entry)
    assert audit_bytes == audit_file.read_bytes()
    assert ExperimentLedger.existing(ledger_path()).committed() == 0


def test_reconciliation_offline_cli_and_inspection_never_create_http_client(
    legacy_incident, monkeypatch, capsys
):
    entry, _ = legacy_incident
    monkeypatch.setattr(
        httpx, "AsyncClient", lambda *a, **k: pytest.fail("Offline reconciliation attempted HTTP")
    )
    assert offline.main(["inspect"]) == 0
    inspected = json.loads(capsys.readouterr().out)
    assert inspected["committed_usd"] == "0.19928000"
    assert inspected["entries"][0]["provider_error_code"] == "credit_balance_exhausted"
    assert inspected["entries"][0]["http_status"] == 429
    args = [
        "reconcile-zero",
        "--reservation-key",
        entry["key"],
        "--request-id",
        REQUEST,
        "--reason",
        REASON,
        "--operator-alias",
        "operator-test",
    ]
    assert offline.main(args) == 1
    assert offline.main(args + ["--confirm-zero-billed"]) == 0
    assert offline.main(["inspect"]) == 0
    assert ExperimentLedger.existing(ledger_path()).committed() == 0


def test_explicit_retry_uses_new_pair_attempt_preserves_failure_and_protects_success(
    legacy_incident,
    pilot,  # noqa: F811
):
    entry, _ = legacy_incident
    config, corpus_data, questions, report, prompts, output = pilot
    confirm(entry)
    fresh = (*pilot[:-1], output.with_name("retry-1"))
    with pytest.raises(ValueError, match="already attempted"):
        invoke(fresh, lambda r: pytest.fail("Implicit retry sent HTTP"))
    import asyncio

    from evaluation.cloud_llm_execution import execute_pair

    calls = []
    result = asyncio.run(
        execute_pair(
            config,
            corpus_data,
            questions,
            report,
            prompts,
            fresh[-1],
            confirmed=True,
            environment={"TENDERCITE_LLM_API_KEY": KEY},
            retry_reconciled=True,
            transport=httpx.MockTransport(
                lambda r: (
                    calls.append(r)
                    or httpx.Response(200, json=completion(questions[0].question_id))
                )
            ),
        )
    )
    assert result["status"] == "COMPLETED_PAIR" and len(calls) == 2
    ledger = ExperimentLedger.existing(ledger_path())
    assert ledger.data["entries"][0] == entry
    new = ledger.data["entries"][1:]
    assert len({e["key"] for e in ledger.data["entries"]}) == 3
    assert {e["attempt_id"] for e in new} == {result["attempt_id"]}
    assert result["attempt_id"] != ledger.attempt_id(entry)
    assert ledger.committed() == Decimal("0.00052")
    with pytest.raises(ValueError, match="already attempted"):
        ledger.assert_pair_new(entry["question_id"], retry_reconciled=True)


def test_retry_requires_confirmation_still_holds_budget_and_does_not_clear_other_uncertain(
    legacy_incident,
    pilot,  # noqa: F811
):
    entry, _ = legacy_incident
    confirm(entry)
    config, corpus_data, questions, _, _, output = pilot
    with experiment_lock(ledger_path()):
        ledger = ExperimentLedger(ledger_path(), config, corpus_data)
        from evaluation.cloud_llm_contexts import build_comparison_questions

        other = build_comparison_questions(corpus_data)[2]
        blocked = ledger.reserve(other, "19.99", output.with_name("other-question"))
        ledger.reconcile(blocked, None)
    with pytest.raises(ValueError, match="uncertain/interrupted"):
        ledger.assert_pair_new(entry["question_id"], retry_reconciled=True)
    assert ledger.committed() == Decimal("19.99")
    ledger.reconcile(blocked, {"usage_derived_cost": "19.99"})
    ledger.assert_pair_new(entry["question_id"], retry_reconciled=True)
    with pytest.raises(ValueError, match="remaining persistent"):
        ledger.reserve(
            questions[0], "0.19928", output.with_name("too-expensive"), retry_reconciled=True
        )


def test_retry_cli_preserves_all_gates_and_does_not_retry_automatically(
    legacy_incident,
    cli_arguments,  # noqa: F811
    pilot,  # noqa: F811
    monkeypatch,
):
    entry, _ = legacy_incident
    confirm(entry)
    args = cli_arguments.copy()
    args[7] = str(pilot[-1].with_name("cli-retry"))
    monkeypatch.setenv("TENDERCITE_LLM_API_KEY", KEY)
    original = cli.execute_pair
    calls = []

    async def mocked(*a, **k):
        return await original(
            *a,
            **k,
            transport=httpx.MockTransport(
                lambda r: (
                    calls.append(r) or httpx.Response(200, json=completion(pilot[2][0].question_id))
                )
            ),
        )

    monkeypatch.setattr(cli, "execute_pair", mocked)
    assert cli.main(args + ["--retry-reconciled"]) == 1
    assert not calls
    args[7] = str(pilot[-1].with_name("cli-retry-authorized"))
    assert cli.main(args + ["--execute", "--confirm-transmission", "--retry-reconciled"]) == 0
    assert len(calls) == 2


def test_retry_flag_cannot_clear_an_unreconciled_attempt_or_start_a_new_pair(legacy_incident):
    entry, _ = legacy_incident
    ledger = ExperimentLedger.existing(ledger_path())
    with pytest.raises(ValueError, match="uncertain/interrupted"):
        ledger.assert_pair_new(entry["question_id"], retry_reconciled=True)
    confirm(entry)
    ledger = ExperimentLedger.existing(ledger_path())
    with pytest.raises(ValueError, match="previously reconciled"):
        ledger.assert_pair_new("never-attempted-question", retry_reconciled=True)


def test_reconciled_source_response_tampering_blocks_budget_release(legacy_incident):
    entry, response = legacy_incident
    confirm(entry)
    response.write_bytes(response.read_bytes() + b" ")
    with pytest.raises(ValueError, match="response changed"):
        ExperimentLedger.existing(ledger_path())


def test_manual_reconciliation_shares_the_execution_lock(legacy_incident):
    entry, _ = legacy_incident
    original = ledger_path().read_bytes()
    with experiment_lock(ledger_path()):
        with pytest.raises(ValueError, match="locked"):
            confirm(entry)
    assert ledger_path().read_bytes() == original


def test_offline_inspection_does_not_create_a_missing_ledger(pilot):  # noqa: F811
    assert not ledger_path().exists()
    assert offline.main(["inspect"]) == 1
    assert not ledger_path().exists()


@pytest.mark.parametrize("missing", ["key", "transmission"])
def test_explicit_retry_still_requires_key_and_transmission_permission(
    legacy_incident,
    pilot,  # noqa: F811
    missing,
):
    entry, _ = legacy_incident
    confirm(entry)
    import asyncio

    from evaluation.cloud_llm_execution import execute_pair

    config, corpus_data, questions, report, prompts, output = pilot
    before = ledger_path().read_bytes()
    with pytest.raises(ValueError, match="Execution blocked"):
        asyncio.run(
            execute_pair(
                config,
                corpus_data,
                questions,
                report,
                prompts,
                output.with_name(f"retry-missing-{missing}"),
                confirmed=missing != "transmission",
                environment={} if missing == "key" else {"TENDERCITE_LLM_API_KEY": KEY},
                retry_reconciled=True,
                transport=httpx.MockTransport(lambda r: pytest.fail("Unapproved retry sent HTTP")),
            )
        )
    assert ledger_path().read_bytes() == before
