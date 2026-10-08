import json
from pathlib import Path

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
    assert doc["chunk_count"] >= doc["page_count"]
    for page_number, text in enumerate(dataset["pages"], start=1):
        page = client.get(f"/api/v1/documents/{doc['id']}/pages/{page_number}").json()
        # This also verifies German umlauts/ß and distractor text survive PDF extraction.
        assert normalize_quote(page["text"]) == normalize_quote(text)
    for case in gold:
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
    assert result["source_span_type_recall"] == 1 / 12
    assert abs(result["unsupported_finding_rate"] - 1 / 3) < 1e-10
    assert result["evidence_verification_rate"] == 1.0
    assert extraction_metrics(gold, [prediction], "other")["matched_count"] == 0
    assert extraction_metrics(gold, [], "d")["unsupported_finding_rate"] is None
    hits = {first["id"]: [{"page_number": 1}]}
    assert retrieval_metrics(gold, hits, 1)["hit_at_k"] == 1 / 12


def test_dataset_covers_both_languages_with_shared_pages_and_distractors():
    dataset = json.loads(Path("evaluation/gold.json").read_text())
    assert dataset["dataset_version"] == "synthetic-tender-2"
    cases = dataset["cases"]
    assert len(cases) == len({case["id"] for case in cases}) == 12
    types = {"DEADLINE", "REFERENCE", "INSURANCE", "CERTIFICATE", "PRICE", "PRIVACY"}
    for language in ("en", "de"):
        selected = [case for case in cases if case["language"] == language]
        assert len(selected) == 6
        assert {case["requirement_type"] for case in selected} == types
        assert all(case["query"].strip() for case in selected)
    assert {case["id"] for case in cases if case["language"] == "en"} == {
        "deadline",
        "references",
        "insurance",
        "certificate",
        "price",
        "privacy",
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
