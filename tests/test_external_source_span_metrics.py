"""Deterministic metrics and chunk diagnostics using original synthetic text only."""

from dataclasses import replace

import pytest

from evaluation.external_source_span_metrics import (
    KS,
    VerifiedSpan,
    aggregate,
    case_metrics,
    scorable_metrics,
)
from evaluation.source_span_structure import diagnose_span, summarize_structure
from tendercite.services.chunking import chunk_page
from tendercite.services.retrieval.base import SearchHit


def span(identifier="one", doc="A", page=1, quote="alpha beta"):
    return VerifiedSpan(identifier, doc + ".pdf", doc, "a" * 64, page, quote)


def hit(text="alpha beta", doc="A", page=1, identifier="chunk-one"):
    return SearchHit(
        chunk_id=identifier,
        document_id=doc,
        document_name=doc + ".pdf",
        page_number=page,
        text=text,
        score=0.9,
    )


@pytest.mark.parametrize("text", ["wrong passage", "alpha", "Alpha beta"])
def test_correct_document_and_page_do_not_prove_source_span(text):
    result = case_metrics([span()], ["A"], [hit(text)])
    assert all(result["page_hit_at_k"].values())
    assert not any(result["source_span_hit_at_k"].values())
    assert not any(result["all_source_documents_supported_at_k"].values())
    assert not any(result["verified_span_recall_at_k"].values())


@pytest.mark.parametrize("changes", [{"doc": "B"}, {"page": 2}])
def test_quote_on_wrong_document_or_page_is_not_a_hit(changes):
    result = case_metrics([span()], ["A"], [hit(**changes)])
    assert not any(result["page_hit_at_k"].values())
    assert not any(result["source_span_hit_at_k"].values())


def test_complete_case_sensitive_whitespace_normalized_quote():
    result = case_metrics([span(quote="alpha\nbeta")], ["A"], [hit("alpha \t beta")])
    assert all(result["source_span_hit_at_k"].values())
    assert all(value == 1 for value in result["verified_span_recall_at_k"].values())
    assert result["first_matching_chunk_rank"] == 1


def test_split_quotes_are_not_assembled_from_two_hits():
    result = case_metrics([span()], ["A"], [hit("alpha"), hit("beta", identifier="two")])
    assert all(result["page_hit_at_k"].values())
    assert not any(result["source_span_hit_at_k"].values())
    assert result["first_matching_chunk_rank_by_span"] == {"one": None}


def test_multiple_spans_documents_ranks_recall_and_duplicate_hits():
    spans = [span(), span("two", "B", quote="gamma"), span("three", page=2, quote="delta")]
    duplicate = hit()
    hits = [hit("unrelated"), duplicate, duplicate, hit("gamma", "B"), hit("delta", page=2)]
    result = case_metrics(spans, ["A", "B"], hits)
    assert result["first_matching_chunk_rank"] == 2
    assert result["first_matching_chunk_rank_by_span"] == {"one": 2, "two": 4, "three": 5}
    assert result["verified_span_recall_at_k"]["1"] == 0
    assert result["verified_span_recall_at_k"]["3"] == pytest.approx(1 / 3)
    assert result["all_source_documents_supported_at_k"]["3"] is False
    assert result["matched_span_ids_at_k"]["3"] == ["one"]
    assert all(result["all_source_documents_supported_at_k"][str(k)] for k in (5, 10, 20, 50))
    assert all(result["verified_span_recall_at_k"][str(k)] == 1 for k in (5, 10, 20, 50))
    assert tuple(map(int, result["page_hit_at_k"])) == KS


def test_empty_results_have_zero_hits_and_recall():
    result = case_metrics([span()], ["A"], [])
    assert not any(result["page_hit_at_k"].values())
    assert not any(result["source_span_hit_at_k"].values())
    assert not any(result["verified_span_recall_at_k"].values())
    assert result["first_matching_chunk_rank"] is None
    assert scorable_metrics([], [hit()])["verified_span_recall_at_k"]["1"] is None
    assert aggregate([])["combined"]["strict"]["source_span_hit_at_k"]["1"] is None


@pytest.mark.parametrize("ks", [(), (0,), (51,), (True,), (1, 1)])
def test_unsupported_cutoffs_are_rejected(ks):
    with pytest.raises(ValueError, match="Chunk k"):
        case_metrics([span()], ["A"], [], ks)


def test_invalid_gold_or_excessive_chunk_pool_is_rejected():
    with pytest.raises(ValueError, match="Every originally required"):
        case_metrics([span()], ["A", "B"], [])
    with pytest.raises(ValueError, match="Every originally required"):
        case_metrics([], ["A"], [])
    with pytest.raises(ValueError, match="Span IDs"):
        case_metrics([span(), span()], ["A"], [])
    with pytest.raises(ValueError, match="empty source quote"):
        case_metrics([span(quote=" \n")], ["A"], [])
    with pytest.raises(ValueError, match="top-50"):
        case_metrics([span()], ["A"], [hit()] * 51)


def test_aggregate_is_question_macro_and_diagnostic_denominator_is_explicit():
    rows = [
        {
            "question_type": "lookup",
            "strict": case_metrics([span()], ["A"], [hit()]),
            "scorable_diagnostic": scorable_metrics([span()], [hit()]),
        },
        {
            "question_type": "reasoning",
            "strict": case_metrics([span(), span("two", quote="gamma")], ["A"], []),
            "scorable_diagnostic": scorable_metrics([], []),
        },
    ]
    result = aggregate(rows)
    combined = result["combined"]
    assert combined["verified_spans"] == 3
    assert combined["strict"]["verified_span_recall_at_k"]["1"] == 0.5
    assert combined["scorable_diagnostic"]["questions_with_scorable_spans"] == 1
    assert combined["scorable_diagnostic"]["verified_span_recall_at_k"]["1"] == 1
    assert (
        result["by_question_type"]["reasoning"]["scorable_diagnostic"]["source_span_hit_at_k"]["1"]
        is None
    )


def test_production_chunks_distinguish_complete_split_and_too_long():
    text = "a" * 1150 + "XYZ" + "b" * 1250
    chunks = [hit(c.text, identifier=str(c.ordinal)) for c in chunk_page(1, text)]
    diagnostics = [
        diagnose_span(span("fits", quote=text[1100:1300]), text, chunks),
        diagnose_span(span("split", quote=text[1000:1250]), text, chunks),
        diagnose_span(span("long", quote=text[100:1500]), text, chunks),
        diagnose_span(span("wrong", quote="invented"), text, chunks),
    ]
    assert [row["status"] for row in diagnostics] == [
        "complete",
        "split_across_chunks",
        "too_long",
        "mapping_inconsistency",
    ]
    assert diagnostics[0]["matching_indexed_chunk_ids"]
    assert not diagnostics[1]["matching_indexed_chunk_ids"]
    assert summarize_structure(diagnostics) == {
        "verified_spans": 4,
        "complete": 1,
        "split_across_chunks": 1,
        "too_long": 1,
        "mapping_inconsistency": 1,
    }


def test_structural_page_and_document_mapping_are_required():
    assert diagnose_span(span(), None, [hit()])["status"] == "mapping_inconsistency"
    assert diagnose_span(span(), "alpha beta", [hit(doc="B")])["status"] == "mapping_inconsistency"
    assert (
        diagnose_span(replace(span(), page_number=2), "alpha beta", [hit()])["status"]
        == "mapping_inconsistency"
    )


def test_strict_denominator_keeps_structurally_impossible_spans():
    text = "a" * 1150 + "XYZ" + "b" * 1250
    chunks = [hit(c.text, identifier=str(c.ordinal)) for c in chunk_page(1, text)]
    spans = [
        span("fits", quote=text[1100:1300]),
        span("split", quote=text[1000:1250]),
        span("long", quote=text[100:1500]),
    ]
    structure = [diagnose_span(s, text, chunks) for s in spans]
    strict = case_metrics(spans, ["A"], chunks)
    diagnostic = scorable_metrics(
        [s for s, d in zip(spans, structure, strict=True) if d["status"] == "complete"], chunks
    )
    assert strict["verified_span_count"] == 3
    assert strict["verified_span_recall_at_k"]["50"] == pytest.approx(1 / 3)
    assert diagnostic["scorable_span_count"] == 1
    assert diagnostic["verified_span_recall_at_k"]["50"] == 1
    assert strict["missed_span_ids_at_k"]["50"] == ["split", "long"]
