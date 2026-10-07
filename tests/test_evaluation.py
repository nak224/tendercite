import json
from pathlib import Path

from evaluation.generate import make_pdf
from evaluation.metrics import extraction_metrics, retrieval_metrics


def test_gold_pdf_and_all_source_quotes(client):
    gold = json.loads(Path("evaluation/gold.json").read_text())["cases"]
    response = client.post(
        "/api/v1/documents",
        files={"file": ("gold.pdf", make_pdf([c["quote"] for c in gold]), "application/pdf")},
    )
    doc = response.json()
    assert doc["page_count"] == 6
    assert doc["chunk_count"] == 6
    for case in gold:
        result = client.post(
            "/api/v1/evidence/validate",
            json={"document_id": doc["id"], "page_number": case["page"], "quote": case["quote"]},
        )
        assert result.json()["grounding_status"] == "VERIFIED_QUOTE"


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
    assert result["source_span_type_recall"] == 1 / 6
    assert abs(result["unsupported_finding_rate"] - 1 / 3) < 1e-10
    assert result["evidence_verification_rate"] == 1.0
    assert extraction_metrics(gold, [prediction], "other")["matched_count"] == 0
    assert extraction_metrics(gold, [], "d")["unsupported_finding_rate"] is None
    hits = {first["id"]: [{"page_number": 1}]}
    assert retrieval_metrics(gold, hits, 1)["hit_at_k"] == 1 / 6
