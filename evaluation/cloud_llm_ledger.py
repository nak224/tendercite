"""Durable reservations and cross-process locks for one local USD experiment."""

import hashlib
import json
import os
import tempfile
from contextlib import contextmanager
from decimal import Decimal, InvalidOperation

from evaluation import external_benchmark
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
            entries = self.data.get("entries")
            if (
                not isinstance(entries, list)
                or not all(isinstance(e, dict) and isinstance(e.get("key"), str) for e in entries)
                or len({e["key"] for e in entries}) != len(entries)
            ):
                raise ValueError("Malformed or duplicate ledger entries")
            for entry in entries:
                amount(entry["reserved_cost"])
                if entry["status"] not in ("RESERVED", "UNCERTAIN", "SETTLED", "OVER_ESTIMATE"):
                    raise ValueError("Invalid ledger accounting state")
                if entry["status"] in ("SETTLED", "OVER_ESTIMATE"):
                    amount(entry["usage_derived_cost"])
        else:
            self.data = {"version": "cloud-llm-ledger-1", "binding": binding, "entries": []}
            atomic_save(self.path, self.data)

    def committed(self):
        return amount(self.data["binding"]["prior_estimated_spend"]) + sum(
            (
                amount(e["usage_derived_cost"])
                if e["status"] in ("SETTLED", "OVER_ESTIMATE")
                else amount(e["reserved_cost"])
            )
            for e in self.data["entries"]
        )

    def assert_pair_new(self, question_id):
        if any(e["status"] != "SETTLED" for e in self.data["entries"]):
            raise ValueError(
                "Ledger has uncertain/interrupted/over-estimate accounting; reconcile first"
            )
        if any(e["question_id"] == question_id for e in self.data["entries"]):
            raise ValueError(
                "Question pair already attempted; automatic repeats/resumes are disabled"
            )

    def reserve(self, question, estimate, output_dir):
        cost = amount(estimate)
        if self.committed() + cost > self.limit:
            raise ValueError("Next request cannot fit the remaining persistent 20 USD budget")
        key = hashlib.sha256(f"{question.question_id}:{question.strategy}".encode()).hexdigest()
        if any(e["key"] == key for e in self.data["entries"]):
            raise ValueError("Request already attempted")
        entry = {
            "key": key,
            "question_id": question.question_id,
            "strategy": question.strategy,
            "reserved_cost": str(cost),
            "status": "RESERVED",
            "reserved_at": timestamp(),
            "output_dir": str(output_dir),
        }
        self.data["entries"].append(entry)
        atomic_save(self.path, self.data)  # Durable BEFORE any network request.
        return entry

    def reconcile(self, entry, accounting):
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
