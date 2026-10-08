import json
from pathlib import Path

import pytest

from evaluation.generate import make_pdf
from evaluation.metrics import extraction_metrics, retrieval_metrics
from tendercite.services.text import normalize_quote


def test_gold_pdf_and_all_source_quotes(client):
    dataset = json.loads(Path("evaluation/gold.json").read_text())
    gold = dataset["cases"]
    response = client.post(
        "/api/v1/documents",
        files={"file": ("gold.pdf", make_pdf(dataset["pages"]), "application/pdf")},
    )
    assert response.status_code == 201
    doc = response.json()
    assert doc["page_count"] == len(dataset["pages"]) == 8
    assert doc["chunk_count"] > doc["page_count"]
    chunks = client.get(f"/api/v1/documents/{doc['id']}/chunks").json()
    relevant_pages = {case["page"] for case in gold}
    for page_number, text in enumerate(dataset["pages"], start=1):
        page = client.get(f"/api/v1/documents/{doc['id']}/pages/{page_number}").json()
        # This also verifies German umlauts/ß and distractor text survive PDF extraction.
        assert normalize_quote(page["text"]) == normalize_quote(text)
        page_chunks = [chunk for chunk in chunks if chunk["page_number"] == page_number]
        if page_number in relevant_pages:
            # Use the production 1200-character chunking default, not a tiny test override.
            assert len(page["text"]) > 1200
            assert len(page_chunks) > 1
        for chunk in page_chunks:
            assert page["text"][chunk["char_start"] : chunk["char_end"]] == chunk["text"]
    for case in gold:
        page_chunks = [chunk for chunk in chunks if chunk["page_number"] == case["page"]]
        correct = next(
            chunk
            for chunk in page_chunks
            if normalize_quote(case["quote"]) in normalize_quote(chunk["text"])
        )
        distractor = next(
            chunk
            for chunk in page_chunks
            if normalize_quote(case["quote"]) not in normalize_quote(chunk["text"])
        )
        # A real wrong chunk from the right fixture page passes only the page metric.
        hits = {case["id"]: [distractor, correct]}
        at_one = retrieval_metrics([case], hits, 1, doc["id"])
        assert at_one["page_hit_at_k"] == 1
        assert at_one["source_span_hit_at_k"] == 0
        assert retrieval_metrics([case], hits, 2, doc["id"])["source_span_hit_at_k"] == 1
        result = client.post(
            "/api/v1/evidence/validate",
            json={"document_id": doc["id"], "page_number": case["page"], "quote": case["quote"]},
        )
        assert result.json()["grounding_status"] == "VERIFIED_QUOTE"
        wrong_page = case["page"] % len(dataset["pages"]) + 1
        invalid = client.post(
            "/api/v1/evidence/validate",
            json={"document_id": doc["id"], "page_number": wrong_page, "quote": case["quote"]},
        )
        assert invalid.json()["grounding_status"] == "INVALID_QUOTE"


def test_metrics_penalize_missing_wrong_and_duplicate_predictions():
    gold = json.loads(Path("evaluation/gold.json").read_text())["cases"]
    first = gold[0]
    prediction = {
        "category": first["category"],
        "requirement_type": first["requirement_type"],
        "evidence": [
            {
                "document_id": "d",
                "page_number": 1,
                "quote": first["quote"],
                "grounding_status": "VERIFIED_QUOTE",
            }
        ],
    }
    unsupported = {"category": "MUST", "requirement_type": "REFERENCE", "evidence": []}
    result = extraction_metrics(gold, [prediction, prediction, unsupported], "d")
    assert result["source_span_type_precision"] == 1 / 3
    assert result["source_span_type_recall"] == 1 / len(gold)
    assert abs(result["unsupported_finding_rate"] - 1 / 3) < 1e-10
    assert result["evidence_verification_rate"] == 1.0
    assert extraction_metrics(gold, [prediction], "other")["matched_count"] == 0
    assert extraction_metrics(gold, [], "d")["unsupported_finding_rate"] is None
    hits = {first["id"]: [{"document_id": "d", "page_number": 1, "text": first["quote"]}]}
    measured = retrieval_metrics(gold, hits, 1, "d")
    assert measured["page_hit_at_k"] == measured["source_span_hit_at_k"] == 1 / len(gold)


def test_dataset_covers_both_languages_with_shared_pages_and_distractors():
    dataset = json.loads(Path("evaluation/gold.json").read_text())
    assert dataset["dataset_version"] == "synthetic-tender-3"
    cases = dataset["cases"]
    assert len(cases) == len({case["id"] for case in cases}) == 14
    types = {"DEADLINE", "REFERENCE", "INSURANCE", "CERTIFICATE", "PRICE", "PRIVACY", "SECURITY"}
    for language in ("en", "de"):
        selected = [case for case in cases if case["language"] == language]
        assert len(selected) == 7
        assert {case["requirement_type"] for case in selected} == types
        assert all(case["query"].strip() for case in selected)
    assert {case["id"] for case in cases if case["language"] == "en"} == {
        "deadline",
        "references",
        "insurance",
        "certificate",
        "price",
        "privacy",
        "security",
    }
    populated_pages = {case["page"] for case in cases}
    assert len(populated_pages) < len(cases)  # multiple requirements share pages
    assert len(populated_pages) < len(dataset["pages"])  # dedicated distractor pages
    for page_number, text in enumerate(dataset["pages"], start=1):
        assert text.strip()
        remaining = text
        for case in cases:
            if case["page"] == page_number:
                assert case["quote"] in text
                remaining = remaining.replace(case["quote"], "")
        assert len(remaining.strip()) > 100  # contextual/noise text, not just gold quotes


@pytest.mark.parametrize(
    "hits,page_hit,span_hit",
    [
        ([{"document_id": "d", "page_number": 2, "text": "Two reference projects."}], 0, 0),
        ([{"document_id": "other", "page_number": 1, "text": "Two reference projects."}], 0, 0),
        ([{"document_id": "d", "page_number": 1, "text": "Unrelated requirements."}], 1, 0),
        ([{"document_id": "d", "page_number": 1, "text": "Two\nreference   projects."}], 1, 1),
        # Partial spans cannot be joined across chunks into an invented hit.
        (
            [
                {"document_id": "d", "page_number": 1, "text": "Two reference"},
                {"document_id": "d", "page_number": 1, "text": "projects."},
            ],
            1,
            0,
        ),
        ([], 0, 0),
    ],
)
def test_retrieval_span_metric_requires_document_page_and_complete_quote(hits, page_hit, span_hit):
    gold = [{"id": "references", "page": 1, "quote": "Two reference projects."}]
    result = retrieval_metrics(gold, {"references": hits}, 2, "d")
    assert result["page_hit_at_k"] == page_hit
    assert result["source_span_hit_at_k"] == span_hit
    assert "hit_at_k" not in result  # ambiguous legacy metric name is intentionally removed


def test_retrieval_metrics_empty_dataset_and_invalid_k():
    result = retrieval_metrics([], {}, 3, "d")
    assert result["queries"] == 0
    assert result["page_hit_at_k"] is None
    assert result["source_span_hit_at_k"] is None
    with pytest.raises(ValueError, match="positive"):
        retrieval_metrics([], {}, 0, "d")
