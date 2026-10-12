"""Durable reservations and cross-process locks for one local USD experiment."""

import hashlib
import json
import os
import re
import tempfile
from contextlib import contextmanager
from decimal import Decimal, InvalidOperation
from pathlib import Path
from uuid import uuid4

from evaluation import external_benchmark
from evaluation.cloud_llm_retry import validate_truncated_response
from evaluation.external_benchmark import confined
from evaluation.external_qa_spans import read_local_json
from evaluation.ted_acquisition import timestamp


def ledger_path():
    # Independent of each fresh output directory: CLI invocations cannot choose a new budget.
    return confined(external_benchmark.EXTERNAL_ROOT / "cloud-llm-experiment" / "ledger.json")


@contextmanager
def experiment_lock(path):
    path = confined(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = confined(path.with_suffix(".lock"))
    descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
    locked = False
    try:
        if os.name == "nt":
            import msvcrt

            if os.fstat(descriptor).st_size == 0:
                os.write(descriptor, b"0")
            os.lseek(descriptor, 0, os.SEEK_SET)
            try:
                msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                raise ValueError("Experiment ledger is locked by another process") from exc
        else:
            import fcntl

            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                raise ValueError("Experiment ledger is locked by another process") from exc
        locked = True
        yield
    finally:
        if locked:
            if os.name == "nt":
                os.lseek(descriptor, 0, os.SEEK_SET)
                msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def atomic_save(path, value):
    path = confined(path)
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix=".ledger-")
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as output:
            json.dump(value, output, ensure_ascii=False, indent=2, allow_nan=False)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
        if os.name != "nt":
            directory = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def amount(value):
    try:
        result = Decimal(value)
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError("Invalid ledger amount") from exc
    if not result.is_finite() or result < 0:
        raise ValueError("Invalid ledger amount")
    return result


def quota_failure(entry, request_id):
    """Match one saved no-usage quota response, including legacy PR #15 output."""
    if entry["status"] != "UNCERTAIN" or entry["strategy"] not in ("e5_rag", "full_context"):
        raise ValueError("Only an uncertain recorded quota failure can be reconciled here")
    path = confined(Path(entry["output_dir"]) / f"{entry['strategy']}.json")
    record, digest = read_local_json(path)
    if not isinstance(record, dict) or not isinstance(record.get("raw_response"), str):
        raise ValueError("Original provider response is missing")
    body = json.loads(record["raw_response"])
    error = body.get("error") if isinstance(body, dict) else None
    if (
        record.get("reservation_key") != entry["key"]
        or record.get("question_id") != entry["question_id"]
        or record.get("strategy") != entry["strategy"]
        or record.get("provider_request_id") != request_id
        or entry.get("provider_request_id", request_id) != request_id
        or record.get("http_status") != 429
        or record.get("accounting_status") != "UNCERTAIN"
        or not isinstance(error, dict)
        or error.get("code") != "credit_balance_exhausted"
        or error.get("type") != "insufficient_quota"
        or not isinstance(error.get("message"), str)
        or body.get("usage") is not None
        or record.get("accounting") is not None
        or (entry.get("response_file_sha256") and entry["response_file_sha256"] != digest)
    ):
        raise ValueError(
            "Request/entry does not match the recorded no-usage credit_balance_exhausted failure"
        )
    return path, digest


class ExperimentLedger:
    def __init__(self, path, config, corpus):
        self.path = confined(path)
        self.limit = Decimal("20")
        binding = {
            "provider": config.base_url,
            "model": config.model,
            "provenance": corpus.provenance,
            "pricing": config.pricing.model_dump(mode="json"),
            "usage_pricing": config.usage_pricing.model_dump(mode="json")
            if config.usage_pricing
            else None,
            "prior_estimated_spend": str(config.spending.prior_estimated_spend),
            "currency": "USD",
            "limit": "20",
        }
        if self.path.exists():
            self.data, _ = read_local_json(self.path)
            if not isinstance(self.data, dict):
                raise ValueError("Malformed experiment ledger")
            if (
                self.data.get("version") != "cloud-llm-ledger-1"
                or self.data.get("binding") != binding
            ):
                raise ValueError("Ledger binding changed; do not reset the existing experiment")
        else:
            self.data = {"version": "cloud-llm-ledger-1", "binding": binding, "entries": []}
            atomic_save(self.path, self.data)
        self.validate_history()

    @classmethod
    def existing(cls, path):
        """Offline access to an existing ledger only; never create/reset an experiment."""
        ledger = cls.__new__(cls)
        ledger.path, ledger.limit = confined(path), Decimal("20")
        ledger.data, _ = read_local_json(ledger.path)
        if (
            not isinstance(ledger.data, dict)
            or ledger.data.get("version") != "cloud-llm-ledger-1"
            or not isinstance(ledger.data.get("binding"), dict)
            or ledger.data["binding"].get("currency") != "USD"
            or amount(ledger.data["binding"].get("limit")) != ledger.limit
        ):
            raise ValueError("Invalid existing 20 USD experiment ledger")
        ledger.validate_history()
        return ledger

    def validate_history(self):
        entries = self.data.get("entries")
        if (
            not isinstance(entries, list)
            or not all(isinstance(e, dict) and isinstance(e.get("key"), str) for e in entries)
            or len({e["key"] for e in entries}) != len(entries)
        ):
            raise ValueError("Malformed or duplicate ledger entries")
        for entry in entries:
            self.validate_staged_metadata(entry["strategy"], entry.get("staged_step"))
            amount(entry["reserved_cost"])
            if entry["status"] not in ("RESERVED", "UNCERTAIN", "SETTLED", "OVER_ESTIMATE"):
                raise ValueError("Invalid ledger accounting state")
            if entry["status"] in ("SETTLED", "OVER_ESTIMATE"):
                amount(entry["usage_derived_cost"])
        self.manual_zero = {}
        pointers = self.data.get("manual_reconciliations", [])
        if not isinstance(pointers, list):
            raise ValueError("Malformed manual reconciliation history")
        for pointer in pointers:
            key = pointer["reservation_key"]
            if key in self.manual_zero:
                raise ValueError("Duplicate manual reconciliation")
            expected_file = f"reconciliations/{key}.json"
            if not re.fullmatch(r"[0-9a-f]{64}", key) or pointer["audit_file"] != expected_file:
                raise ValueError("Invalid reconciliation audit path")
            audit, digest = read_local_json(self.path.parent / expected_file)
            originals = [entry for entry in entries if entry["key"] == key]
            if (
                digest != pointer["audit_sha256"]
                or not isinstance(audit, dict)
                or len(originals) != 1
                or audit.get("original_entry") != originals[0]
                or audit.get("schema_version") != "cloud-llm-reconciliation-1"
                or audit.get("provider_error_code") != "credit_balance_exhausted"
                or audit.get("confirmed_zero_billed") is not True
                or amount(audit.get("confirmed_billed_usd")) != 0
                or originals[0]["status"] != "UNCERTAIN"
            ):
                raise ValueError("Manual reconciliation audit does not match immutable history")
            path, response_hash = quota_failure(originals[0], audit["provider_request_id"])
            if (
                response_hash != audit["response_file_sha256"]
                or str(path) != audit["response_file"]
            ):
                raise ValueError("Reconciled original response changed")
            self.manual_zero[key] = audit

    @staticmethod
    def attempt_id(entry):
        return entry.get("attempt_id") or f"legacy:{entry['key']}"

    def effective_status(self, entry):
        return "MANUALLY_RECONCILED_ZERO" if entry["key"] in self.manual_zero else entry["status"]

    def committed(self):
        return amount(self.data["binding"]["prior_estimated_spend"]) + sum(
            self.entry_cost(e) for e in self.data["entries"]
        )

    def entry_cost(self, entry):
        if entry["key"] in self.manual_zero:
            return Decimal("0")
        return amount(
            entry["usage_derived_cost"]
            if entry["status"] in ("SETTLED", "OVER_ESTIMATE")
            else entry["reserved_cost"]
        )

    @staticmethod
    def validate_staged_metadata(strategy, metadata):
        staged = strategy.startswith(("staged_extract_v1_", "staged_synthesis_v1_"))
        if not staged and metadata is None:
            return
        if not staged or not isinstance(metadata, dict):
            raise ValueError("Staged request requires its own namespaced step metadata")
        stage = metadata.get("stage")
        prefix = "staged_extract_v1_" if stage == "extraction" else "staged_synthesis_v1_"
        if (
            metadata.get("version") != "staged-ledger-step-1"
            or stage not in ("extraction", "synthesis")
            or any(
                not re.fullmatch(r"[0-9a-f]{64}", str(metadata.get(key, "")))
                for key in (
                    "plan_id",
                    "step_id",
                    "request_payload_sha256",
                    "input_source_sha256",
                    "configuration_sha256",
                    "plan_file_sha256",
                )
            )
            or strategy != prefix + metadata["step_id"]
            or (
                stage == "extraction"
                and not re.fullmatch(r"batch-[0-9a-f]{24}", str(metadata.get("batch_id", "")))
            )
            or (stage == "synthesis" and metadata.get("batch_id") is not None)
        ):
            raise ValueError("Invalid staged step identity/provenance")
        confined(Path(metadata["plan_dir"]))

    def assert_accounting_ready(self):
        if any(
            self.effective_status(e) not in ("SETTLED", "MANUALLY_RECONCILED_ZERO")
            for e in self.data["entries"]
        ):
            raise ValueError("Ledger has uncertain/interrupted/over-estimate accounting")

    def assert_pair_new(self, question_id, *, retry_reconciled=False):
        if any(
            self.effective_status(e) not in ("SETTLED", "MANUALLY_RECONCILED_ZERO")
            for e in self.data["entries"]
        ):
            raise ValueError(
                "Ledger has uncertain/interrupted/over-estimate accounting; reconcile first"
            )
        prior = [
            e
            for e in self.data["entries"]
            if e["question_id"] == question_id and e["strategy"] in ("e5_rag", "full_context")
        ]
        if retry_reconciled and not prior:
            raise ValueError("Explicit retry requires a previously reconciled failed attempt")
        if prior and (not retry_reconciled or any(e["key"] not in self.manual_zero for e in prior)):
            raise ValueError(
                "Question pair already attempted; automatic repeats/resumes are disabled"
            )

    def assert_truncated_retry(self, question, original_attempt_id, corpus, config):
        if any(
            self.effective_status(e) not in ("SETTLED", "MANUALLY_RECONCILED_ZERO")
            for e in self.data["entries"]
        ):
            raise ValueError(
                "Uncertain/interrupted/over-estimate accounting blocks truncation retry"
            )
        prior = [
            e
            for e in self.data["entries"]
            if e["question_id"] == question.question_id and e["strategy"] == question.strategy
        ]
        matched = [e for e in prior if self.attempt_id(e) == original_attempt_id]
        if len(matched) != 1 or matched[0]["status"] != "SETTLED":
            raise ValueError("Exact settled original question/strategy/attempt is required")
        entry = matched[0]
        if prior[-1] is not entry:
            raise ValueError("Original strategy already has a subsequent attempt; inspect latest")
        return validate_truncated_response(entry, question, corpus, config)

    def reserve(
        self,
        question,
        estimate,
        output_dir,
        *,
        attempt_id=None,
        retry_reconciled=False,
        truncated_retry_key=None,
        staged_step=None,
    ):
        self.validate_staged_metadata(question.strategy, staged_step)
        cost = amount(estimate)
        if self.committed() + cost > self.limit:
            raise ValueError("Next request cannot fit the remaining persistent 20 USD budget")
        prior = [
            e
            for e in self.data["entries"]
            if e["question_id"] == question.question_id and e["strategy"] == question.strategy
        ]
        truncation_retry = bool(
            truncated_retry_key
            and prior
            and prior[-1]["key"] == truncated_retry_key
            and prior[-1]["status"] == "SETTLED"
        )
        if (
            prior
            and not truncation_retry
            and (not retry_reconciled or any(e["key"] not in self.manual_zero for e in prior))
        ):
            raise ValueError("Request already attempted")
        attempt_id = attempt_id or str(uuid4())
        key = hashlib.sha256(
            f"{attempt_id}:{question.question_id}:{question.strategy}".encode()
        ).hexdigest()
        if any(e["key"] == key for e in self.data["entries"]):
            raise ValueError("Attempt already reserved")
        entry = {
            "key": key,
            "attempt_id": attempt_id,
            "question_id": question.question_id,
            "strategy": question.strategy,
            "reserved_cost": str(cost),
            "status": "RESERVED",
            "reserved_at": timestamp(),
            "output_dir": str(output_dir),
        }
        if staged_step is not None:
            entry["staged_step"] = staged_step
        if truncated_retry_key:
            if not truncation_retry:
                raise ValueError("Original truncation reservation does not match latest strategy")
            entry["retry_of_key"] = truncated_retry_key
            entry["retry_of_attempt"] = self.attempt_id(prior[-1])
        self.data["entries"].append(entry)
        atomic_save(self.path, self.data)  # Durable BEFORE any network request.
        return entry

    def reconcile(self, entry, accounting, *, record=None):
        if record is not None:
            entry["provider_request_id"] = record.get("provider_request_id")
            entry["http_status"] = record.get("http_status")
            entry["provider_error_code"] = record.get("provider_error_code")
            _, entry["response_file_sha256"] = read_local_json(
                Path(entry["output_dir"]) / f"{entry['strategy']}.json"
            )
        if accounting is None:
            entry["status"] = "UNCERTAIN"  # Reservation remains committed.
        else:
            actual = amount(accounting["usage_derived_cost"])
            entry["usage_derived_cost"] = str(actual)
            entry["status"] = (
                "OVER_ESTIMATE" if actual > amount(entry["reserved_cost"]) else "SETTLED"
            )
        entry["reconciled_at"] = timestamp()
        atomic_save(self.path, self.data)
