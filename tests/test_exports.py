import csv
import io

from test_analysis import create_run


def test_exports_preserve_review_and_evidence(client, retrieval):
    run = create_run(client).json()
    fid = run["findings"][0]["id"]
    value = {
        "statement": "=SUM(1,2) | <script>\n[link](http://bad)",
        "category": "MUST",
        "requirement_type": "REFERENCE",
    }
    client.post(
        "/api/v1/findings/" + fid + "/reviews", json={"action": "MODIFY", "reviewed_value": value}
    )
    client.patch(
        "/api/v1/go-no-go/" + fid, json={"status": "PARTIAL", "rationale": "One reference provided"}
    )
    result = client.get("/api/v1/exports?format=json").json()
    item = result["findings"][0]
    assert item["effective_value"] == value
    assert item["statement"] != value["statement"]
    assert item["assessment"]["status"] == "PARTIAL"
    assert item["review_history"][0]["action"] == "MODIFY"
    assert item["evidence"][0]["document_name"] == "tender.pdf"
    response = client.get("/api/v1/exports?format=csv")
    assert "attachment;" in response.headers["content-disposition"]
    row = list(csv.DictReader(io.StringIO(response.text)))[0]
    assert row["effective_statement"].startswith("'=SUM")
    assert row["quote"] == item["evidence"][0]["quote"]
    assert row["page_number"] == "1"
    assert row["evidence_grounding_status"] == "VERIFIED_QUOTE"
    markdown = client.get("/api/v1/exports?format=markdown").text
    assert "<script>" not in markdown
    assert "\\|" in markdown and "\\[link\\]" in markdown
    assert "PARTIAL" in markdown
    assert client.get("/api/v1/exports?format=docx").status_code == 422
    assert client.get("/api/v1/exports?analysis_run_id=unknown").json()["findings"] == []


def test_missing_evidence_is_exported_explicitly(client, retrieval):
    create_run(client, "missing")
    rows = list(csv.DictReader(io.StringIO(client.get("/api/v1/exports?format=csv").text)))
    assert len(rows) == 1
    assert rows[0]["grounding_status"] == "MISSING_EVIDENCE"
    assert rows[0]["assessment_status"] == ""
