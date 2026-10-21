"""In-process HTTP tests with generated PDFs and disposable SQLite only."""

import copy
import json
from concurrent.futures import ThreadPoolExecutor

import pytest
from curated_intake_helpers import QUOTE, intake, intake_body, repository, upload

from tendercite.api.dependencies import get_llm, get_retrieval
from tendercite.domain.curated_intake import MAX_INTAKE_BYTES, intake_analysis_id
from tendercite.main import app

BASE = "/api/v1/curated-intakes"


def post(client, operation, body):
    payload = body if operation == "validate" else {"intake": body, "confirm_manual_intake": True}
    return client.post(f"{BASE}/{operation}", json=payload)


def test_validation_reuses_existing_summary_without_database_writes(client):
    document = upload(client)
    repo = repository(client)
    before = repo.database_path.read_bytes()
    response = post(client, "validate", intake_body(document))
    assert response.status_code == 200
    assert response.json() == repo.validate_curated_intake(intake(document)).model_dump(mode="json")
    assert repo.database_path.read_bytes() == before
    assert repo.get_analysis(response.json()["analysis_id"]) is None
    assert repo.list_findings() == []


@pytest.mark.parametrize("confirmation", [False, None, "true", 1, 0, "missing"])
def test_commit_requires_literal_boolean_confirmation(client, confirmation):
    body = {"intake": intake_body(upload(client))}
    if confirmation != "missing":
        body["confirm_manual_intake"] = confirmation
    repo = repository(client)
    before = repo.database_path.read_bytes()
    response = client.post(f"{BASE}/commit", json=body)
    assert response.status_code == 422
    assert response.json() == {"detail": {"code": "MANUAL_INTAKE_CONFIRMATION_REQUIRED"}}
    assert repo.database_path.read_bytes() == before
    assert repo.list_findings() == []


def test_commit_is_normal_unreviewed_analysis_and_preserves_later_reviews(client):
    document = upload(client)
    body = intake_body(document)
    response = post(client, "commit", body)
    assert response.status_code == 200
    result = response.json()
    assert result["status"] == "CREATED"
    assert result["analysis_id"] == intake_analysis_id(intake(document))
    run = client.get(f"/api/v1/analyses/{result['analysis_id']}").json()
    finding = client.get("/api/v1/findings", params={"analysis_run_id": run["id"]}).json()[0]
    assert finding["review_status"] == "UNREVIEWED"
    assert finding["confidence"] is None
    assert finding["grounding_status"] == "VERIFIED_QUOTE"
    assert finding["statement"] == QUOTE
    assert finding["evidence"][0]["document_id"] == document["id"]
    assert run["provider"] == "manual_operator_entry" and run["model"] == "not_invoked"
    assert not run["curated_intake"]["provider_invoked"]
    repo = repository(client)
    assert repo.list_reviews(finding["id"]) == []
    review = client.post(
        f"/api/v1/findings/{finding['id']}/reviews",
        json={"action": "CONFIRM", "comment": "Synthetic simulation, not a human review"},
    )
    assert review.status_code == 201
    before = repo.database_path.read_bytes()
    matrix = repo.list_matrix()
    repeated = post(client, "commit", body)
    assert repeated.status_code == 200
    assert repeated.json() == result | {"status": "ALREADY_PRESENT"}
    assert repo.database_path.read_bytes() == before
    assert repo.list_matrix() == matrix
    assert client.get(f"/api/v1/findings/{finding['id']}").json()["review_status"] == "CONFIRMED"
    assert client.get(f"/api/v1/findings/{finding['id']}/reviews").json() == [review.json()]


@pytest.mark.parametrize("operation", ["validate", "commit"])
@pytest.mark.parametrize(
    ("failure", "status", "code"),
    [
        ("quote", 422, "SOURCE_QUOTE_NOT_VERIFIED"),
        ("hash", 409, "SOURCE_PDF_HASH_MISMATCH"),
        ("missing", 404, "SOURCE_DOCUMENT_NOT_FOUND"),
        ("page", 404, "SOURCE_PAGE_NOT_FOUND"),
    ],
)
def test_source_errors_are_safe_and_all_or_nothing(client, operation, failure, status, code):
    body = intake_body(upload(client))
    if failure == "quote":
        body["findings"][0]["evidence"][0]["quote"] = "PRIVATE absent quotation"
    elif failure == "hash":
        body["documents"][0]["expected_pdf_sha256"] = "0" * 64
    elif failure == "missing":
        body["documents"][0]["document_id"] = "missing"
        body["findings"][0]["evidence"][0]["document_id"] = "missing"
    else:
        body["findings"][0]["evidence"][0]["page_number"] = 99
    repo = repository(client)
    before = repo.database_path.read_bytes()
    response = post(client, operation, body)
    assert response.status_code == status
    assert response.json() == {"detail": {"code": code}}
    assert "PRIVATE" not in response.text and QUOTE not in response.text
    assert str(repo.database_path) not in response.text
    assert repo.database_path.read_bytes() == before
    assert repo.list_findings() == []


@pytest.mark.parametrize("operation", ["validate", "commit"])
@pytest.mark.parametrize("failure", ["version", "extra", "path", "url", "type", "duplicates"])
def test_invalid_contract_is_sanitized(client, operation, failure):
    body = intake_body(upload(client))
    if failure == "version":
        body["schema_version"] = "PRIVATE unsupported"
    elif failure in ("extra", "path", "url"):
        body[{"extra": "unexpected", "path": "input", "url": "url"}[failure]] = "PRIVATE"
    elif failure == "type":
        body["findings"][0]["evidence"][0]["page_number"] = "1"
    else:
        body["findings"].append(copy.deepcopy(body["findings"][0]))
    response = post(client, operation, body)
    assert response.status_code == 422
    assert response.json() == {"detail": {"code": "INVALID_CURATED_INTAKE"}}
    assert "PRIVATE" not in response.text and QUOTE not in response.text
    assert repository(client).list_findings() == []


@pytest.mark.parametrize("operation", ["validate", "commit"])
@pytest.mark.parametrize("content", [b"{PRIVATE", b'{"purpose":1,"purpose":2}', b"NaN", b"\xff"])
def test_invalid_json_and_duplicate_keys(client, operation, content):
    response = client.post(
        f"{BASE}/{operation}", content=content, headers={"Content-Type": "application/json"}
    )
    assert response.status_code == 422
    assert response.json() == {"detail": {"code": "INVALID_CURATED_INTAKE"}}


@pytest.mark.parametrize("operation", ["validate", "commit"])
@pytest.mark.parametrize("headers", [{}, {"Content-Length": "1"}])
def test_raw_size_limit_is_enforced_even_with_false_length(client, operation, headers):
    response = client.post(
        f"{BASE}/{operation}",
        content=b" " * (MAX_INTAKE_BYTES + 1),
        headers={"Content-Type": "application/json"} | headers,
    )
    assert response.status_code == 413
    assert response.json() == {"detail": {"code": "INTAKE_TOO_LARGE"}}


def test_streamed_size_limit_without_content_length(client):
    response = client.post(
        f"{BASE}/validate",
        content=iter([b" " * (MAX_INTAKE_BYTES // 2), b" " * (MAX_INTAKE_BYTES // 2 + 1)]),
        headers={"Content-Type": "application/json", "Transfer-Encoding": "chunked"},
    )
    assert response.status_code == 413


@pytest.mark.parametrize(
    "headers, status",
    [
        ({"Content-Type": "text/plain"}, 415),
        ({"Content-Type": "application/json", "Content-Encoding": "gzip"}, 415),
        ({"Content-Type": "application/json", "Content-Length": "PRIVATE"}, 400),
    ],
)
def test_transport_errors_are_safe(client, headers, status):
    response = client.post(f"{BASE}/validate", content=b"{}", headers=headers)
    assert response.status_code == status and "PRIVATE" not in response.text


def test_commit_revalidates_and_rejects_changed_sources(client):
    body = intake_body(upload(client))
    assert post(client, "validate", body).status_code == 200
    repo = repository(client)
    with repo._connect() as conn:
        conn.execute("UPDATE documents SET sha256=?", ("f" * 64,))
    response = post(client, "commit", body)
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "SOURCE_PDF_HASH_MISMATCH"
    assert repo.list_findings() == []


def test_changed_stored_page_cannot_silently_replace_previous_commit(client):
    body = intake_body(upload(client))
    created = post(client, "commit", body).json()
    repo = repository(client)
    with repo._connect() as conn:
        conn.execute("UPDATE pages SET text=text || ' More synthetic context.' WHERE page_number=1")
    before = repo.database_path.read_bytes()
    response = post(client, "commit", body)
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "INTAKE_SOURCE_STATE_CHANGED"
    assert repo.database_path.read_bytes() == before
    assert repo.get_analysis(created["analysis_id"]) is not None


def test_concurrent_http_commits_keep_existing_idempotency(client):
    body = intake_body(upload(client))
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: post(client, "commit", body), range(2)))
    assert all(r.status_code == 200 for r in responses)
    assert sorted(r.json()["status"] for r in responses) == ["ALREADY_PRESENT", "CREATED"]
    assert len(repository(client).list_findings()) == 1


@pytest.mark.parametrize("operation", ["validate", "commit"])
def test_unexpected_repository_errors_do_not_leak(client, monkeypatch, operation):
    body = intake_body(upload(client))

    def fail(_):
        raise RuntimeError("PRIVATE database path and quoted text")

    monkeypatch.setattr(repository(client), f"{operation}_curated_intake", fail)
    response = post(client, operation, body)
    assert response.status_code == 500
    assert response.json() == {"detail": {"code": "CURATED_INTAKE_FAILED"}}


def test_no_inference_dependencies_and_typed_openapi(client):
    body = intake_body(upload(client))

    def fail():
        pytest.fail("Inference dependency must not run")

    app.dependency_overrides[get_llm] = fail
    app.dependency_overrides[get_retrieval] = fail
    assert post(client, "validate", body).status_code == 200
    assert post(client, "commit", body).status_code == 200
    schema = client.get("/openapi.json").json()
    for operation in ("validate", "commit"):
        route = schema["paths"][f"{BASE}/{operation}"]["post"]
        assert route["requestBody"]["required"]
        assert "application/json" in route["requestBody"]["content"]
        assert route["responses"]["422"]["content"]["application/json"]["schema"] == {
            "$ref": "#/components/schemas/IntakeApiError"
        }
    models = schema["components"]["schemas"]
    commit = models["CuratedIntakeCommit"]
    assert commit["additionalProperties"] is False
    assert commit["properties"]["confirm_manual_intake"]["const"] is True
    assert "CuratedTenderIntake" in json.dumps(commit["properties"]["intake"])
