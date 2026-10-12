"""Deterministic full-source partitioning using the existing complete-wire estimator."""

import hashlib
import json
from decimal import Decimal

from evaluation.cloud_llm_contexts import QAAnswer
from evaluation.cloud_llm_preflight import (
    ComparisonConfig,
    estimate_input_tokens,
    preflight,
    prepared_payload,
)
from evaluation.external_benchmark import confined
from evaluation.external_qa_spans import read_local_json, write_new_json
from evaluation.staged_evidence_schemas import (
    EXTRACTION_PROMPT_VERSION,
    EXTRACTION_SCHEMA_VERSION,
    SYNTHESIS_PROMPT_VERSION,
    SYNTHESIS_SCHEMA_VERSION,
    AtomicExtraction,
    PreparedStep,
    SourceSegment,
    StagedSettings,
)

PLAN_VERSION = "staged-evidence-plan-1"


def digest(value):
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(raw).hexdigest()


def text_hash(text):
    return hashlib.sha256(text.encode()).hexdigest()


def stage_config(config, settings, stage):
    data = config.model_dump(mode="json")
    data["max_output_tokens"] = getattr(settings, f"{stage}_max_output_tokens")
    return ComparisonConfig.model_validate(data)


def page_inventory(corpus):
    return [
        {
            "document_name": name,
            "document_id": doc_id,
            "pdf_sha256": corpus.documents[name]["sha256"],
            "page_number": number,
            "characters": len(text),
            "text_sha256": text_hash(text),
        }
        for name, doc_id in sorted(corpus.mapping.items())
        for number, text in sorted(corpus.pages[doc_id].items())
    ]


def segment(corpus, page, start, end, index):
    text = corpus.pages[page["document_id"]][page["page_number"]][start:end]
    return SourceSegment(
        source_id=f"{page['document_id']}:page:{page['page_number']}:chars:{start}-{end}",
        document_id=page["document_id"],
        document_name=page["document_name"],
        pdf_sha256=page["pdf_sha256"],
        page_number=page["page_number"],
        chunk_id=None,
        text=text,
        segment_index=index,
        char_start=start,
        char_end=end,
        text_sha256=text_hash(text),
    )


def prepared_extraction(corpus, question_id, plan_id, sources):
    batch_id = "batch-" + digest([s.model_dump() for s in sources])[:24]
    step_id = digest([plan_id, "extraction", batch_id])
    question = corpus.questions[question_id]
    return PreparedStep(
        question_id=question_id,
        question=question.question,
        question_type=question.question_type,
        strategy="staged_extract_v1_" + step_id,
        stage="extraction",
        step_id=step_id,
        batch_id=batch_id,
        sources=sources,
    )


def partition(corpus, question_id, config, settings, plan_id):
    """Pack whole pages per document; split only an individually oversized page, losslessly."""
    if question_id not in corpus.questions:
        raise ValueError("Exact original eligible question ID is required")
    effective = stage_config(config, settings, "extraction")
    budget = min(
        settings.max_estimated_input_tokens,
        effective.usable_context_tokens - effective.max_output_tokens,
    )
    batches, current = [], []

    def fits(sources):
        step = prepared_extraction(corpus, question_id, plan_id, sources)
        payload = prepared_payload(effective, step, response_model=AtomicExtraction)
        tokens, _ = estimate_input_tokens(payload, effective)
        return tokens <= budget

    def flush():
        if current:
            batches.append(prepared_extraction(corpus, question_id, plan_id, current.copy()))
            current.clear()

    for page in page_inventory(corpus):
        whole = segment(corpus, page, 0, page["characters"], 1)
        if current and (
            current[0].document_id != whole.document_id
            or len({s.page_number for s in current} | {whole.page_number})
            > settings.max_pages_per_batch
            or not fits([*current, whole])
        ):
            flush()
        if fits([whole]):
            current.append(whole)
            continue
        if not whole.text:
            raise ValueError("Input budget cannot accommodate even an empty page and schema")
        start, index = 0, 1
        while start < len(whole.text):
            low, high, best = start + 1, len(whole.text), start
            while low <= high:
                end = (low + high) // 2
                if fits([segment(corpus, page, start, end, index)]):
                    best, low = end, end + 1
                else:
                    high = end - 1
            if best == start:
                raise ValueError("Input budget cannot accommodate source identifiers and schema")
            # Prefer a natural boundary, without stripping even whitespace from source text.
            if best < len(whole.text):
                boundary = max(
                    whole.text.rfind("\n", start, best), whole.text.rfind(" ", start, best)
                )
                if boundary >= start + (best - start) // 2:
                    best = boundary + 1
            part = segment(corpus, page, start, best, index)
            batches.append(prepared_extraction(corpus, question_id, plan_id, [part]))
            start, index = best, index + 1
    flush()
    if not batches:
        raise ValueError("No parsed pages to partition")
    return batches


def plan(corpus, question_id, config, settings):
    if config.repetitions != 1:
        raise ValueError("Staged planning requires one request per step, without repetitions")
    inventory = page_inventory(corpus)
    identity = {
        "version": PLAN_VERSION,
        "configuration": config.model_dump(mode="json"),
        "settings": settings.model_dump(),
        "provenance": corpus.provenance,
        "question_id": question_id,
        "question": corpus.questions[question_id].question,
        "pages": inventory,
        "prompt_versions": [EXTRACTION_PROMPT_VERSION, SYNTHESIS_PROMPT_VERSION],
        "schema_versions": [EXTRACTION_SCHEMA_VERSION, SYNTHESIS_SCHEMA_VERSION],
    }
    plan_id = digest(identity)
    steps = partition(corpus, question_id, config, settings, plan_id)
    effective = stage_config(config, settings, "extraction")
    report, requests = preflight(
        effective, corpus, steps, environment={}, response_model=AtomicExtraction
    )
    for page in inventory:
        page["batch_ids"] = [
            step.batch_id
            for step in steps
            if any(
                (s.document_id, s.page_number) == (page["document_id"], page["page_number"])
                for s in step.sources
            )
        ]
    descriptors = []
    for number, (step, row) in enumerate(zip(steps, report["request_preflight"], strict=True), 1):
        descriptors.append(
            {
                "batch_id": step.batch_id,
                "step_id": step.step_id,
                "strategy": step.strategy,
                "sequence": number,
                "document_name": step.sources[0].document_name,
                "document_id": step.sources[0].document_id,
                "page_range": [step.sources[0].page_number, step.sources[-1].page_number],
                "sources": [s.model_dump(exclude={"text"}) for s in step.sources],
                "source_sha256": digest([s.model_dump() for s in step.sources]),
                "prompt_version": EXTRACTION_PROMPT_VERSION,
                "schema_version": EXTRACTION_SCHEMA_VERSION,
                "configuration_sha256": digest(effective.model_dump(mode="json")),
                "preflight": row,
            }
        )
    synthesis = stage_config(config, settings, "synthesis")
    input_ceiling = settings.synthesis_max_estimated_input_tokens
    synthesis_cost = (
        (
            Decimal(input_ceiling) * synthesis.pricing.input_per_million
            + Decimal(synthesis.max_output_tokens) * synthesis.pricing.output_per_million
        )
        / 1_000_000
        * synthesis.spending.safety_multiplier
    )
    total = Decimal(report["estimated_experiment_cost_with_margin"]) + synthesis_cost
    failures = report["failures"].copy()
    if input_ceiling + synthesis.max_output_tokens > synthesis.usable_context_tokens:
        failures.append("synthesis_context_limit_exceeded")
    if synthesis_cost > synthesis.spending.per_request:
        failures.append("synthesis_per_request_cost_limit_exceeded")
    if total + config.spending.prior_estimated_spend > config.spending.cumulative:
        failures.append("full_staged_plan_exceeds_configured_cumulative_budget")
    manifest = {
        **identity,
        "plan_id": plan_id,
        "configuration_sha256": digest(config.model_dump(mode="json")),
        "pages": inventory,
        "batches": descriptors,
        "planned_extraction_requests": len(steps),
        "planned_optional_synthesis_requests": 1,
        "planned_requests_including_optional_synthesis": len(steps) + 1,
        "estimated_extraction_cost_with_margin": report["estimated_experiment_cost_with_margin"],
        "optional_synthesis_cost_ceiling_with_margin": str(synthesis_cost),
        "full_plan_cost_ceiling_with_margin": str(total),
        "synthesis_input_token_ceiling": input_ceiling,
        "synthesis_payload_status": "PENDING_VALIDATED_EXTRACTION_NOT_AN_ACTUAL_INPUT_ESTIMATE",
        "currency": config.pricing.currency,
        "status": "BLOCKED_PREFLIGHT" if failures else "READY_DRY_RUN",
        "failures": failures,
        "api_calls": 0,
        "embeddings_called": False,
        "human_gold_used_for_source_selection": False,
    }
    return manifest, steps, requests


def save_plan(directory, manifest, requests):
    directory = confined(directory)
    directory.mkdir(parents=True, exist_ok=False)
    write_new_json(directory / "plan.json", manifest)
    for request in requests:
        batch_id = request["prepared"]["batch_id"]
        write_new_json(directory / "requests" / f"{batch_id}.json", request)


def load_plan(directory, corpus):
    directory = confined(directory)
    saved, file_hash = read_local_json(directory / "plan.json")
    config = ComparisonConfig.model_validate(saved["configuration"])
    settings = StagedSettings.model_validate(saved["settings"])
    fresh, steps, requests = plan(corpus, saved["question_id"], config, settings)
    if fresh != saved:
        raise ValueError("Saved staged plan differs from current source/configuration provenance")
    for step, request in zip(steps, requests, strict=True):
        persisted, _ = read_local_json(directory / "requests" / f"{step.batch_id}.json")
        if persisted != request:
            raise ValueError("Saved extraction payload differs from the preflight builder")
    return fresh, config, settings, steps, requests, file_hash


def synthesis_preflight(config, settings, corpus, step):
    effective = stage_config(config, settings, "synthesis")
    report, requests = preflight(effective, corpus, [step], environment={}, response_model=QAAnswer)
    if report["request_preflight"][0]["input_token_estimate"] > (
        settings.synthesis_max_estimated_input_tokens
    ):
        report["status"] = "BLOCKED_PREFLIGHT"
        report["failures"].append("synthesis_evidence_exceeds_budget; no findings dropped")
    report["prompt_version"] = SYNTHESIS_PROMPT_VERSION
    report["response_schema_version"] = SYNTHESIS_SCHEMA_VERSION
    return effective, report, requests
