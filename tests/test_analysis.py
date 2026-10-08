import json

from conftest import make_pdf

from tendercite.api.dependencies import get_llm
from tendercite.domain.analysis import ExtractionResult
from tendercite.main import app


class FakeLLM:
    model = "deterministic-test-model"
    provider = "fake"
    mode = "valid"

    async def generate_structured(self, *, user_prompt, response_model, **kwargs):
        hit = json.loads(user_prompt)["sources"][0]
        evidence = {key: hit[key] for key in ("chunk_id", "document_id", "page_number")}
        evidence["quote"] = hit["text"]
        if self.mode == "wrong_chunk":
            evidence["chunk_id"] = "fabricated"
        if self.mode == "wrong_page":
            evidence["page_number"] = 999
        if self.mode == "wrong_quote":
            evidence["quote"] = "Invented requirement"
        if self.mode == "whitespace":
            evidence["quote"] = evidence["quote"].replace(" ", "\n")
        if self.mode == "blank":
            evidence["quote"] = " "
        return response_model.model_validate(
            {
                "findings": [
                    {
                        "statement": "Two references required",
                        "category": "MUST",
                        "requirement_type": "REFERENCE",
                        "confidence": 0.9,
                        "evidence": [] if self.mode == "missing" else [evidence],
                    }
                ]
            }
        )


def create_run(client, mode="valid"):
    llm = FakeLLM()
    llm.mode = mode
    app.dependency_overrides[get_llm] = lambda: llm
    doc = client.post(
        "/api/v1/documents",
        files={
            "file": (
                "tender.pdf",
                make_pdf(["Two reference projects are required."]),
                "application/pdf",
            )
        },
    ).json()
    return client.post("/api/v1/analyses", json={"document_ids": [doc["id"]], "query": "reference"})


def test_grounded_extraction_roundtrip(client, retrieval):
    response = create_run(client)
    assert response.status_code == 201, response.text
    run = response.json()
    assert run["provider"] == "fake"
    assert run["prompt_version"]
    assert run["schema_version"]
    finding = run["findings"][0]
    assert finding["grounding_status"] == "VERIFIED_QUOTE"
    assert finding["evidence"][0]["char_start"] == 0
    assert client.get("/api/v1/analyses/" + run["id"]).json() == run
    assert client.get("/api/v1/findings/" + finding["id"]).json() == finding
    assert len(client.get("/api/v1/findings", params={"analysis_run_id": run["id"]}).json()) == 1
    assert (
        client.delete("/api/v1/documents/" + run["request"]["document_ids"][0]).status_code == 409
    )


def test_fabricated_and_missing_evidence_explicitly_marked(client, retrieval):
    for mode in ["wrong_chunk", "wrong_page", "wrong_quote", "missing", "blank"]:
        response = create_run(client, mode)
        assert response.status_code == 201, response.text
        finding = response.json()["findings"][0]
        expected = "MISSING_EVIDENCE" if mode in ("missing", "blank") else "INVALID_QUOTE"
        assert finding["grounding_status"] == expected


def test_unconfigured_provider_and_invalid_schema(client):
    import pytest
    from pydantic import ValidationError

    app.dependency_overrides.pop(get_llm, None)
    assert client.get("/api/v1/analyses/unknown").status_code == 404
    with pytest.raises(ValidationError):
        ExtractionResult.model_validate({"findings": [{"statement": "x", "category": "invented"}]})


def test_original_candidate_survives_quote_normalization(client, retrieval):
    finding = create_run(client, "whitespace").json()["findings"][0]
    assert finding["grounding_status"] == "VERIFIED_QUOTE"
    assert "\n" in finding["original_output"]["evidence"][0]["quote"]
    assert "\n" not in finding["evidence"][0]["quote"]
    client.post("/api/v1/findings/" + finding["id"] + "/reviews", json={"action": "CONFIRM"})
    current = client.get("/api/v1/findings/" + finding["id"]).json()
    assert current["original_output"] == finding["original_output"]
