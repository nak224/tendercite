"""Offline proof for one settled, truncated request; never a retry dispatcher."""

import hashlib
import json
import re
from pathlib import Path

from evaluation.cloud_llm_preflight import ComparisonConfig, estimate_input_tokens, prepared_payload
from evaluation.cloud_llm_usage import account_usage
from evaluation.external_qa_spans import read_local_json


def validate_truncated_response(entry, question, corpus, config):
    path = Path(entry["output_dir"]) / f"{entry['strategy']}.json"
    record, digest = read_local_json(path)
    if not isinstance(record, dict):
        raise ValueError("Original retry response must be an object")
    if not isinstance(record.get("preflight_estimate"), dict):
        raise ValueError("Original truncation preflight is missing or malformed")
    old = ComparisonConfig.model_validate(record["configuration"])
    allowed_changes = {"max_output_tokens", "model_capabilities"}
    if config.model_dump(exclude=allowed_changes) != old.model_dump(exclude=allowed_changes):
        raise ValueError("Truncation retry permits only an explicit output-cap/capability change")
    if config.max_output_tokens <= old.max_output_tokens:
        raise ValueError("Truncation retry requires an explicitly increased output-token limit")
    _, wire = estimate_input_tokens(prepared_payload(old, question), old)
    if (
        record.get("reservation_key") != entry["key"]
        or record.get("question_id") != question.question_id
        or record.get("strategy") != question.strategy
        or record.get("http_status") != 200
        or record.get("response_validity") != "INVALID_OR_INCOMPLETE"
        or record.get("completion_status") != "length"
        or record.get("generation_status", "OUTPUT_TOKEN_TRUNCATION") != "OUTPUT_TOKEN_TRUNCATION"
        or record.get("accounting_status") != "USAGE_RECORDED_NOT_INVOICE"
        or record.get("source_provenance") != corpus.provenance
        or record.get("request_payload_sha256") != hashlib.sha256(wire).hexdigest()
        or record.get("preflight_estimate", {}).get("max_output_tokens") != old.max_output_tokens
        or record.get("provider_request_id")
        != entry.get("provider_request_id", record.get("provider_request_id"))
        or (entry.get("attempt_id") and record.get("attempt_id") != entry["attempt_id"])
        or (entry.get("response_file_sha256") and entry["response_file_sha256"] != digest)
    ):
        raise ValueError("Original truncation response, source or attempt metadata mismatch")
    body = json.loads(record["raw_response"])
    if not isinstance(body, dict) or body.get("error"):
        raise ValueError("Original response is not an unambiguous provider completion")
    model = body.get("model")
    if model != old.model and not (
        isinstance(model, str) and re.fullmatch(re.escape(old.model) + r"-\d{4}-\d{2}-\d{2}", model)
    ):
        raise ValueError("Original truncation has an unexpected provider model")
    choices = body.get("choices")
    if (
        not isinstance(choices, list)
        or len(choices) != 1
        or not isinstance(choices[0], dict)
        or choices[0].get("finish_reason") != "length"
        or not isinstance(choices[0].get("message"), dict)
        or choices[0]["message"].get("refusal")
    ):
        raise ValueError("Original provider completion does not prove output truncation")
    accounting = account_usage(body.get("usage"), old)
    if (
        record.get("accounting") != accounting
        or accounting["usage_derived_cost"] != entry["usage_derived_cost"]
        or accounting["completion_tokens"] > old.max_output_tokens
    ):
        raise ValueError("Original truncation usage does not match settled ledger accounting")
    return {
        "original_reservation_key": entry["key"],
        "original_attempt_id": entry.get("attempt_id") or f"legacy:{entry['key']}",
        "original_response_file": str(path),
        "original_response_sha256": digest,
        "original_output_limit": old.max_output_tokens,
        "configured_output_limit": config.max_output_tokens,
        "configuration_difference": "explicitly_increased_output_cap",
    }
