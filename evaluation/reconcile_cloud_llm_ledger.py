"""Offline inspection and explicit human-confirmed zero-cost quota reconciliation only."""

import argparse
import copy
import json
import logging
import re
from pathlib import Path

from evaluation.cloud_llm_ledger import (
    ExperimentLedger,
    atomic_save,
    experiment_lock,
    ledger_path,
    quota_failure,
)
from evaluation.external_qa_spans import read_local_json, write_new_json
from evaluation.ted_acquisition import timestamp


def summary(ledger):
    entries = []
    for entry in ledger.data["entries"]:
        request_id = entry.get("provider_request_id")
        http_status = entry.get("http_status")
        error_code = entry.get("provider_error_code")
        if not request_id:
            try:
                record, _ = read_local_json(Path(entry["output_dir"]) / f"{entry['strategy']}.json")
                request_id = record.get("provider_request_id")
                http_status = record.get("http_status")
                body = json.loads(record.get("raw_response", "{}"))
                error = body.get("error") if isinstance(body, dict) else None
                error_code = error.get("code") if isinstance(error, dict) else None
            except (OSError, ValueError, KeyError, TypeError, AttributeError):
                request_id = None
        entries.append(
            {
                "reservation_key": entry["key"],
                "attempt_id": ledger.attempt_id(entry),
                "question_id": entry["question_id"],
                "strategy": entry["strategy"],
                "historical_status": entry["status"],
                "effective_status": ledger.effective_status(entry),
                "original_reserved_usd": entry["reserved_cost"],
                "committed_usd": str(ledger.entry_cost(entry)),
                "provider_request_id": request_id,
                "http_status": http_status,
                "provider_error_code": error_code,
                "output_dir": entry["output_dir"],
            }
        )
    return {
        "ledger": str(ledger.path),
        "budget_usd": "20",
        "committed_usd": str(ledger.committed()),
        "entries": entries,
        "reconciliation_count": len(ledger.manual_zero),
        "api_calls": 0,
    }


def reconcile_zero(reservation_key, request_id, *, confirmed=False, reason, operator_alias):
    if not confirmed:
        raise ValueError("Explicit --confirm-zero-billed dashboard confirmation is required")
    if not re.fullmatch(r"[0-9a-f]{64}", reservation_key) or not request_id.strip():
        raise ValueError("An exact reservation key and provider request ID are required")
    if not reason.strip() or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", operator_alias):
        raise ValueError(
            "A nonblank reconciliation reason and non-personal operator alias are required"
        )
    path = ledger_path()
    with experiment_lock(path):
        ledger = ExperimentLedger.existing(path)
        if reservation_key in ledger.manual_zero:
            raise ValueError("This reservation has already been reconciled")
        entries = [e for e in ledger.data["entries"] if e["key"] == reservation_key]
        if len(entries) != 1:
            raise ValueError("Original reservation key not found")
        entry = entries[0]
        response_path, response_hash = quota_failure(entry, request_id)
        relative = f"reconciliations/{reservation_key}.json"
        audit_path = path.parent / relative
        if audit_path.exists():
            # Recovery after an audit write succeeded but atomic ledger publication failed.
            # No overwrite, new timestamp or release without the same explicit confirmation.
            audit, audit_hash = read_local_json(audit_path)
            if (
                audit.get("original_entry") != entry
                or audit.get("provider_request_id") != request_id
                or audit.get("response_file_sha256") != response_hash
                or audit.get("reason") != reason
                or audit.get("operator_alias") != operator_alias
                or audit.get("confirmed_zero_billed") is not True
                or audit.get("confirmed_billed_usd") != "0"
            ):
                raise ValueError("Existing immutable audit differs from this confirmation")
        else:
            _, before_hash = read_local_json(path)
            audit = {
                "schema_version": "cloud-llm-reconciliation-1",
                "recorded_at": timestamp(),
                "original_entry": copy.deepcopy(entry),
                "affected_attempt": ledger.attempt_id(entry),
                "provider_request_id": request_id,
                "provider_error_code": "credit_balance_exhausted",
                "confirmed_zero_billed": True,
                "confirmed_billed_usd": "0",
                "currency": "USD",
                "reason": reason,
                "operator_alias": operator_alias,
                "ledger_before_sha256": before_hash,
                "response_file": str(response_path),
                "response_file_sha256": response_hash,
                "billing_authority": "operator_checked_provider_dashboard",
            }
            write_new_json(audit_path, audit)  # Exclusive, append-only record; never rewritten.
            _, audit_hash = read_local_json(audit_path)
        ledger.data.setdefault("manual_reconciliations", []).append(
            {"reservation_key": reservation_key, "audit_file": relative, "audit_sha256": audit_hash}
        )
        ledger.validate_history()
        atomic_save(path, ledger.data)  # Only this publication releases the reservation.
        return summary(ledger)


def main(argv=None):
    cli = argparse.ArgumentParser(description=__doc__)
    modes = cli.add_subparsers(dest="command", required=True)
    modes.add_parser("inspect", help="Read effective accounting and preserved historical entries")
    zero = modes.add_parser("reconcile-zero", help="Confirm one no-usage quota failure billed zero")
    zero.add_argument("--reservation-key", required=True)
    zero.add_argument("--request-id", required=True)
    zero.add_argument("--confirm-zero-billed", action="store_true")
    zero.add_argument("--reason", required=True)
    zero.add_argument("--operator-alias", required=True)
    args = cli.parse_args(argv)
    try:
        if args.command == "inspect":
            with experiment_lock(ledger_path()):
                result = summary(ExperimentLedger.existing(ledger_path()))
        else:
            result = reconcile_zero(
                args.reservation_key,
                args.request_id,
                confirmed=args.confirm_zero_billed,
                reason=args.reason,
                operator_alias=args.operator_alias,
            )
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        logging.error("Offline ledger operation rejected: %s", exc)
        return 1
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
