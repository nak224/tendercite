"""Original generated PDFs, deterministic source partitions and synthetic atomic records only."""

import copy

import pytest
from test_cloud_llm_contexts import comparison  # noqa: F401
from test_cloud_llm_execution import openai_config
from test_external_document_metrics import corpus  # noqa: F401
from test_external_qa_spans import context  # noqa: F401
from test_external_source_span_runner import inputs  # noqa: F401

from evaluation.cloud_llm_preflight import estimate_input_tokens, prepared_payload
from evaluation.staged_evidence_consolidation import (
    consolidate,
    prepared_synthesis,
    validate_extraction,
    validate_synthesis,
)
from evaluation.staged_evidence_plan import (
    load_plan,
    plan,
    save_plan,
    stage_config,
    synthesis_preflight,
    unpriced_partition,
)
from evaluation.staged_evidence_schemas import AtomicExtraction, StagedSettings


@pytest.fixture
def staged(comparison):  # noqa: F811 - original generated PDF corpus.
    config = openai_config()
    settings = StagedSettings(
        max_pages_per_batch=1, extraction_max_output_tokens=100, synthesis_max_output_tokens=100
    )
    qid = next(iter(comparison.questions))
    manifest, steps, requests = plan(comparison, qid, config, settings)
    return comparison, config, settings, manifest, steps, requests


def finding(step, *, purpose="A stated synthetic compliance purpose", relationship="REFERENCE"):
    source = step.sources[0]
    return {
        "finding_id": "original-finding-1",
        "kind": "LEGAL_REFERENCE",
        "subject": "Test Act",
        "exact_identifier": "Test Act § 4",
        "purpose": purpose,
        "relationship": relationship,
        "uncertainty": None,
        "evidence": [
            {
                "source_id": source.source_id,
                "document_id": source.document_id,
                "pdf_sha256": source.pdf_sha256,
                "page_number": source.page_number,
                "quote": source.text,
            }
        ],
    }


def extraction(step, findings=None):
    return AtomicExtraction.model_validate(
        {
            "question_id": step.question_id,
            "batch_id": step.batch_id,
            "status": "FINDINGS" if findings else "NO_RELEVANT_FINDINGS",
            "findings": findings or [],
            "no_findings_reason": None if findings else "No relevant text.",
        }
    )


def accepted(step, corpus_data, findings=None):
    answer = extraction(step, findings)
    validation = validate_extraction(answer, step, corpus_data)
    return {
        "attempt_id": "original-attempt-" + step.batch_id,
        "response_file_sha256": "a" * 64,
        "completed": validation["extraction_valid"],
        "record": {
            "structured_answer": answer.model_dump(),
            "citation_validation": validation,
            "generation_status": "VALID_STRUCTURED_ANSWER",
            "accounting_status": "USAGE_RECORDED_NOT_INVOICE",
        },
    }


def test_plan_is_reproducible_full_family_and_includes_all_pages(staged):
    corpus_data, config, settings, manifest, steps, requests = staged
    assert plan(corpus_data, manifest["question_id"], config, settings) == (
        manifest,
        steps,
        requests,
    )
    assert manifest["api_calls"] == 0 and not manifest["embeddings_called"]
    assert manifest["planned_extraction_requests"] == 3
    assert manifest["planned_requests_including_optional_synthesis"] == 4
    assert {p["document_name"] for p in manifest["pages"]} == set(corpus_data.documents)
    assert all(p["batch_ids"] for p in manifest["pages"])
    assert not manifest["human_gold_used_for_source_selection"]
    assert len({s.strategy for s in steps}) == len(steps)
    assert all(":" not in s.strategy for s in steps)  # Portable response filenames on Windows.
    for step in steps:
        source = step.sources[0]
        assert source.text == corpus_data.pages[source.document_id][source.page_number]
        assert source.chunk_id is None and source.char_start == 0


def test_unpriced_preview_uses_identical_batching_without_fabricating_prices(staged, monkeypatch):
    import httpx

    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **k: pytest.fail("Preview attempted HTTP"))
    corpus_data, _, settings, manifest, steps, _ = staged
    preview, requests = unpriced_partition(corpus_data, manifest["question_id"], settings)
    assert preview["status"] == "UNPRICED_PREVIEW" and preview["estimated_cost"] is None
    assert preview["api_calls"] == 0 and not preview["api_execution_available"]
    assert [b["batch_id"] for b in preview["batches"]] == [s.batch_id for s in steps]
    assert preview["planned_extraction_requests"] == len(requests) == len(steps)


def test_oversized_pages_split_losslessly_with_complete_wire_budget(staged):
    corpus_data, config, settings, manifest, _, _ = staged
    doc_id = next(iter(corpus_data.mapping.values()))
    text = "Original multilingual distractor Ä text.\n" * 1000
    corpus_data.pages[doc_id][1] = text
    settings.max_estimated_input_tokens = 10_000
    manifest, steps, requests = plan(corpus_data, manifest["question_id"], config, settings)
    parts = [
        s for step in steps for s in step.sources if (s.document_id, s.page_number) == (doc_id, 1)
    ]
    assert len(parts) > 1
    assert "".join(s.text for s in parts) == text
    assert parts[0].char_start == 0 and parts[-1].char_end == len(text)
    assert all(a.char_end == b.char_start for a, b in zip(parts, parts[1:], strict=False))
    effective = stage_config(config, settings, "extraction")
    for step, request in zip(steps, requests, strict=True):
        payload = prepared_payload(effective, step, response_model=AtomicExtraction)
        assert payload == request["payload"]
        assert estimate_input_tokens(payload, effective)[0] <= 10_000
        assert payload["response_format"]["json_schema"]["strict"] is True
        assert "Return JSON matching this schema" in payload["messages"][1]["content"]


def test_page_packing_respects_document_and_page_boundaries(staged):
    corpus_data, config, settings, manifest, _, _ = staged
    settings.max_pages_per_batch = 6
    _, steps, _ = plan(corpus_data, manifest["question_id"], config, settings)
    assert len(steps) == 2
    assert all(len({s.document_id for s in step.sources}) == 1 for step in steps)


def test_blank_page_is_in_inventory_and_has_an_explicit_empty_outcome(staged):
    corpus_data, config, settings, manifest, _, _ = staged
    doc_id = next(iter(corpus_data.mapping.values()))
    corpus_data.pages[doc_id][9] = ""
    manifest, steps, _ = plan(corpus_data, manifest["question_id"], config, settings)
    blank = next(step for step in steps if step.sources[0].page_number == 9)
    assert blank.sources[0].text == ""
    assert validate_extraction(extraction(blank), blank, corpus_data)["empty_batch"]
    assert next(p for p in manifest["pages"] if p["page_number"] == 9)["batch_ids"] == [
        blank.batch_id
    ]


def test_too_small_budget_fails_without_source_truncation(staged):
    corpus_data, config, settings, manifest, _, _ = staged
    before = copy.deepcopy(corpus_data.pages)
    settings.max_estimated_input_tokens = 1
    with pytest.raises(ValueError, match="schema"):
        plan(corpus_data, manifest["question_id"], config, settings)
    assert corpus_data.pages == before


def test_saved_plan_and_request_tampering_are_rejected(staged, tmp_path):
    corpus_data, config, settings, manifest, steps, requests = staged
    folder = tmp_path / "immutable-plan"
    save_plan(folder, manifest, requests)
    assert load_plan(folder, corpus_data)[:5] == (manifest, config, settings, steps, requests)
    with pytest.raises(FileExistsError):
        save_plan(folder, manifest, requests)
    (folder / "requests" / f"{steps[0].batch_id}.json").write_text("{}")
    with pytest.raises(ValueError, match="payload"):
        load_plan(folder, corpus_data)


@pytest.mark.parametrize("bad", ["quote", "page", "document", "hash", "source_id", "empty"])
def test_atomic_citations_validate_actual_supplied_segment_and_stored_page(staged, bad):
    corpus_data, _, _, _, steps, _ = staged
    step = steps[0]
    record = finding(step)
    citation = record["evidence"][0]
    if bad == "quote":
        citation["quote"] = "Invented unsupported original quote"
    elif bad == "page":
        citation["page_number"] += 100
    elif bad == "document":
        citation["document_id"] = "unknown-document"
    elif bad == "hash":
        citation["pdf_sha256"] = "f" * 64
    elif bad == "source_id":
        citation["source_id"] = "different-segment"
    else:
        citation["quote"] = " "
    result = validate_extraction(extraction(step, [record]), step, corpus_data)
    assert not result["extraction_valid"]
    assert result["invalid_citations"] == 1 and result["citations"][0]["failure_reason"]


def test_correct_extraction_and_missing_evidence_and_duplicate_ids(staged):
    corpus_data, _, _, _, steps, _ = staged
    step = steps[0]
    f = finding(step)
    assert validate_extraction(extraction(step, [f]), step, corpus_data)["extraction_valid"]
    result = validate_extraction(extraction(step, [f, f]), step, corpus_data)
    assert (
        not result["extraction_valid"]
        and result["findings"][1]["failure_reason"] == "duplicate_finding_id"
    )
    f["evidence"] = []
    assert not validate_extraction(extraction(step, [f]), step, corpus_data)["extraction_valid"]


@pytest.mark.parametrize("identity", ["question_id", "batch_id"])
def test_wrong_identity_is_not_a_successful_empty_batch(staged, identity):
    corpus_data, _, _, _, steps, _ = staged
    answer = extraction(steps[0])
    setattr(answer, identity, "wrong-id")
    result = validate_extraction(answer, steps[0], corpus_data)
    assert not result["extraction_valid"] and not result["empty_batch"]


def test_exact_quote_cannot_be_whitespace_repaired(staged):
    corpus_data, _, _, _, steps, _ = staged
    f = finding(steps[0])
    f["evidence"][0]["quote"] = f["evidence"][0]["quote"].replace(" ", "\n", 1)
    assert not validate_extraction(extraction(steps[0], [f]), steps[0], corpus_data)[
        "extraction_valid"
    ]


def test_distinguishes_empty_failed_and_missing_batches(staged):
    corpus_data, _, _, manifest, steps, _ = staged
    attempts = {
        steps[0].batch_id: accepted(steps[0], corpus_data),
        steps[1].batch_id: {
            "completed": False,
            "attempt_id": "failed",
            "record": {"generation_status": "OUTPUT_TOKEN_TRUNCATION"},
        },
    }
    result = consolidate(manifest, attempts)
    assert result["completed_batches"] == 1 and result["status"] == "INCOMPLETE"
    assert result["no_relevant_findings_batches"] == [steps[0].batch_id]
    assert result["failed_batches"][0]["batch_id"] == steps[1].batch_id
    assert result["missing_batches"] == [steps[2].batch_id]
    assert result["source_pages_completed"] == 1 and result["source_pages_planned"] == 3
    with pytest.raises(ValueError, match="every planned"):
        prepared_synthesis(manifest, result, corpus_data)


def test_consolidation_deduplicates_formatting_retains_citations_and_distinct_purposes(staged):
    corpus_data, _, _, manifest, steps, _ = staged
    attempts = {}
    for step in steps:
        f = finding(step)
        if step == steps[1]:
            f.update(subject=" TEST   ACT ", exact_identifier="Test   Act § 4")
        if step == steps[2]:
            f["purpose"] = "A separate original definition purpose"
        attempts[step.batch_id] = accepted(step, corpus_data, [f])
    result = consolidate(manifest, attempts)
    assert result["status"] == "COMPLETE" and result["raw_finding_count"] == 3
    assert result["duplicate_findings_removed"] == 1 and result["consolidated_finding_count"] == 2
    assert result["unique_consolidated_reference_count"] == 1
    assert len(result["findings"][0]["evidence"]) == 2
    assert len(result["findings"][0]["origins"]) == 2
    assert len(result["raw_records"]) == 3


def test_different_provisions_do_not_merge_and_ambiguities_are_reported(staged):
    corpus_data, _, _, manifest, steps, _ = staged
    attempts = {}
    for n, step in enumerate(steps):
        f = finding(step)
        if n == 1:
            f["exact_identifier"] = "Test Act § 40"
        if n == 2:
            f.update(subject="Different Named Act", uncertainty="Identifier may be ambiguous")
        attempts[step.batch_id] = accepted(step, corpus_data, [f])
    result = consolidate(manifest, attempts)
    assert result["unique_consolidated_reference_count"] == 3
    assert {i["kind"] for i in result["issues"]} == {
        "EXPLICIT_UNCERTAINTY",
        "AMBIGUOUS_IDENTIFIER_NAMES",
    }


def test_conflicting_relationships_remain_separate_for_human_review(staged):
    corpus_data, _, _, manifest, steps, _ = staged
    attempts = {
        s.batch_id: accepted(
            s, corpus_data, [finding(s, relationship=("EXCLUSION" if i == 1 else "OBLIGATION"))]
        )
        for i, s in enumerate(steps)
    }
    result = consolidate(manifest, attempts)
    assert result["consolidated_finding_count"] == 2
    assert result["issues"][0]["kind"] == "POTENTIAL_RELATIONSHIP_CONFLICT"


def test_case_sensitive_provision_labels_are_not_collapsed(staged):
    corpus_data, _, _, manifest, steps, _ = staged
    attempts = {}
    for i, step in enumerate(steps):
        f = finding(step)
        f["exact_identifier"] = "Test Act § 4(a)" if i == 0 else "Test Act § 4(A)"
        attempts[step.batch_id] = accepted(step, corpus_data, [f])
    result = consolidate(manifest, attempts)
    assert result["unique_consolidated_reference_count"] == 2


def test_synthesis_uses_only_verified_evidence_and_fails_oversized_context(staged):
    corpus_data, config, settings, manifest, steps, _ = staged
    attempts = {s.batch_id: accepted(s, corpus_data, [finding(s)]) for s in steps}
    consolidated = consolidate(manifest, attempts)
    synthesis = prepared_synthesis(manifest, consolidated, corpus_data)
    assert [s.text for s in synthesis.sources] == [s.sources[0].text for s in steps]
    assert synthesis.question == corpus_data.questions[synthesis.question_id].question
    effective, report, _ = synthesis_preflight(config, settings, corpus_data, synthesis)
    assert report["status"] == "READY_DRY_RUN"
    settings.synthesis_max_estimated_input_tokens = 1
    _, blocked, _ = synthesis_preflight(config, settings, corpus_data, synthesis)
    assert blocked["status"] == "BLOCKED_PREFLIGHT"
    assert consolidated["raw_finding_count"] == 3  # Never drop records to fit.
    from evaluation.cloud_llm_contexts import QAAnswer

    source = synthesis.sources[0]
    answer = QAAnswer(
        question_id=synthesis.question_id,
        status="ANSWERED",
        answer="Synthetic.",
        evidence=[
            {
                "document_id": source.document_id,
                "page_number": source.page_number,
                "quote": source.text,
                "chunk_id": None,
            }
        ],
    )
    assert validate_synthesis(answer, synthesis, corpus_data)["citation_status"] == "VERIFIED_QUOTE"
    answer.evidence[0].quote = "Unsupported legal conclusion"
    assert validate_synthesis(answer, synthesis, corpus_data)["citation_status"] == "INVALID_QUOTE"


def test_synthesis_cannot_use_stored_page_text_outside_extracted_quotes(staged):
    corpus_data, _, _, manifest, steps, _ = staged
    attempts = {}
    for step in steps:
        f = finding(step)
        f["evidence"][0]["quote"] = step.sources[0].text[:3]
        attempts[step.batch_id] = accepted(step, corpus_data, [f])
    synthesis = prepared_synthesis(manifest, consolidate(manifest, attempts), corpus_data)
    from evaluation.cloud_llm_contexts import QAAnswer

    source = steps[0].sources[0]
    answer = QAAnswer(
        question_id=synthesis.question_id,
        status="ANSWERED",
        answer="Original synthetic answer.",
        evidence=[
            {
                "document_id": source.document_id,
                "page_number": source.page_number,
                "quote": source.text,
                "chunk_id": None,
            }
        ],
    )
    assert validate_synthesis(answer, synthesis, corpus_data)["citation_status"] == "INVALID_QUOTE"
