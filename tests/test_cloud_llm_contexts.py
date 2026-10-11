"""Original synthetic PDFs and recorded fixture chunks; no models or HTTP requests."""

import json

import pytest
from test_external_benchmark import FAMILY, REVISION
from test_external_document_metrics import corpus  # noqa: F401
from test_external_qa_spans import context  # noqa: F401
from test_external_source_span_runner import inputs  # noqa: F401

from evaluation.cloud_llm_contexts import (
    QAAnswer,
    QACitation,
    SourceText,
    build_comparison_questions,
    load_comparison_corpus,
    validate_answer,
)


@pytest.fixture
def comparison(inputs):  # noqa: F811 - pytest injects the original PDF fixture.
    return load_comparison_corpus(
        inputs["acquisition"],
        inputs["retrieval"],
        family=FAMILY,
        revision=REVISION,
        expected_cases=2,
        expected_documents=2,
    )


def response(prepared, evidence=None, **changes):
    source = prepared.sources[0]
    return QAAnswer.model_validate(
        {
            "question_id": prepared.question_id,
            "status": "ANSWERED",
            "answer": "Synthetic answer.",
            "evidence": [
                QACitation(
                    document_id=source.document_id,
                    page_number=source.page_number,
                    quote="The supplier must provide two references",
                    chunk_id=source.chunk_id,
                ).model_dump()
            ]
            if evidence is None
            else evidence,
            **changes,
        }
    )


def test_both_arms_share_original_questions_instructions_schema_and_all_pages(comparison):
    prepared = build_comparison_questions(comparison, top_k=1)
    assert len(prepared) == 4
    for rag, full in zip(prepared[::2], prepared[1::2], strict=True):
        assert (rag.strategy, full.strategy) == ("e5_rag", "full_context")
        assert rag.question == full.question == comparison.questions[rag.question_id].question
        assert rag.prompts()["system_prompt"] == full.prompts()["system_prompt"]
        assert len(rag.sources) == 1 and len(full.sources) == 3
        assert all(s.chunk_id == s.source_id for s in rag.sources)
        assert all(
            s.chunk_id is None and s.source_id == f"{s.document_id}:page:{s.page_number}"
            for s in full.sources
        )
        assert {s.document_name for s in full.sources} == {"notice.pdf", "contract.pdf"}
        assert all(s.text == comparison.pages[s.document_id][s.page_number] for s in full.sources)
        for question in (rag, full):
            prompt = json.loads(question.prompts()["user_prompt"])
            assert "answer" not in prompt and "source_documents" not in prompt
            assert "Two comparable references." not in json.dumps(prompt)
            assert "strategy" not in prompt
    # The lookup references notice.pdf only: contract.pdf remains in full-context anyway.
    assert {s.document_name for s in prepared[1].sources} == set(comparison.mapping)
    assert not comparison.provenance["human_gold_used_for_context_selection"]


def test_default_top12_preserves_order_and_cutoff_without_reretrieval(comparison):
    qid = next(iter(comparison.questions))
    hits = comparison.hits[qid]
    comparison.hits[qid] = (hits * 5)[:15]
    rag = build_comparison_questions(comparison)[0]
    assert [s.chunk_id for s in rag.sources] == [h.chunk_id for h in comparison.hits[qid][:12]]


@pytest.mark.parametrize("k", [0, 51, True])
def test_invalid_rag_cutoff_is_rejected(comparison, k):
    with pytest.raises(ValueError):
        build_comparison_questions(comparison, top_k=k)


@pytest.mark.parametrize("strategy_index", [0, 1])
def test_valid_quotes_match_page_and_this_context_but_do_not_prove_semantics(
    comparison, strategy_index
):
    prepared = build_comparison_questions(comparison)[strategy_index]
    answer = response(prepared)
    # Match the production whitespace-normalized convention; retain case.
    answer.evidence[0].quote = "The supplier\n must provide two references"
    result = validate_answer(answer, prepared, comparison)
    assert result["citation_status"] == "VERIFIED_QUOTE"
    assert result["semantic_correctness"] == "NOT_INDEPENDENTLY_ESTABLISHED"


@pytest.mark.parametrize(
    "changes",
    [
        {"document_id": "unknown"},
        {"page_number": 99},
        {"chunk_id": "unknown-chunk"},
        {"quote": "a fabricated quote"},
        {"quote": "the supplier must provide two references"},
    ],
)
def test_invalid_citations_cannot_use_native_or_unavailable_sources(comparison, changes):
    prepared = build_comparison_questions(comparison)[0]
    answer = response(prepared)
    answer.evidence[0] = QACitation.model_validate({**answer.evidence[0].model_dump(), **changes})
    assert validate_answer(answer, prepared, comparison)["citation_status"] == "INVALID_QUOTE"


def test_valid_page_quote_outside_supplied_rag_chunk_is_invalid(comparison):
    prepared = build_comparison_questions(comparison, top_k=1)[0]
    answer = response(prepared)
    prepared.sources[0] = SourceText.model_validate(
        {**prepared.sources[0].model_dump(), "text": "Different excerpt."}
    )
    assert validate_answer(answer, prepared, comparison)["citation_status"] == "INVALID_QUOTE"


def test_exposed_quote_must_still_match_the_stored_page(comparison):
    prepared = build_comparison_questions(comparison)[0]
    answer = response(prepared)
    comparison.pages[answer.evidence[0].document_id][answer.evidence[0].page_number] = (
        "Different page."
    )
    result = validate_answer(answer, prepared, comparison)
    assert result["citation_status"] == "INVALID_QUOTE"
    assert result["citations"][0]["failure_reason"] == "quote_not_in_stored_page"


def test_missing_evidence_empty_quote_inability_and_question_identity(comparison):
    prepared = build_comparison_questions(comparison)[0]
    for answer in [
        response(prepared, []),
        response(
            prepared,
            [],
            status="UNABLE_TO_ANSWER",
            answer="",
            inability_reason="No supporting source.",
        ),
    ]:
        assert (
            validate_answer(answer, prepared, comparison)["citation_status"] == "MISSING_EVIDENCE"
        )
    answer = response(prepared)
    answer.evidence[0].quote = " "
    assert validate_answer(answer, prepared, comparison)["citation_status"] == "MISSING_EVIDENCE"
    answer = response(prepared, question_id="wrong-question")
    assert validate_answer(answer, prepared, comparison)["citation_status"] == "INVALID_QUOTE"


def test_response_schema_requires_explicit_ability_metadata(comparison):
    prepared = build_comparison_questions(comparison)[0]
    with pytest.raises(ValueError, match="reason"):
        response(prepared, status="UNABLE_TO_ANSWER")
    with pytest.raises(ValueError, match="nonblank answer"):
        response(prepared, answer=" ")


def test_unreferenced_family_document_is_not_filtered_using_labels(comparison):
    comparison.mapping["distractor.pdf"] = "distractor"
    comparison.pages["distractor"] = {1: "Original synthetic procurement-like distractor text."}
    comparison.documents["distractor.pdf"] = {"sha256": "c" * 64}
    full = build_comparison_questions(comparison)[1]
    assert any(s.document_id == "distractor" for s in full.sources)


def test_empty_rag_keeps_question_and_complete_full_context(comparison):
    qid = next(iter(comparison.questions))
    comparison.hits[qid] = []
    rag, full = build_comparison_questions(comparison)[:2]
    assert rag.sources == [] and full.sources
    assert rag.question_id == full.question_id


def test_local_input_provenance_and_full_cohort_are_required(inputs):  # noqa: F811
    with pytest.raises(ValueError, match="cohort"):
        load_comparison_corpus(
            inputs["acquisition"], inputs["retrieval"], family=FAMILY, revision=REVISION
        )
    (inputs["acquisition"] / f"artifacts/data/{FAMILY}/notice.pdf").write_bytes(b"tampered")
    with pytest.raises(ValueError):
        load_comparison_corpus(
            inputs["acquisition"],
            inputs["retrieval"],
            family=FAMILY,
            revision=REVISION,
            expected_cases=2,
            expected_documents=2,
        )
