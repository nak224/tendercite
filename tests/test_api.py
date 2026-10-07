from io import BytesIO

from fastapi.testclient import TestClient
from pypdf import PdfWriter

from tendercite.api.dependencies import get_repository
from tendercite.core.config import settings
from tendercite.main import app
from tendercite.repositories.sqlite import SqliteRepository


def _blank_pdf() -> bytes:
    buffer = BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    writer.write(buffer)
    return buffer.getvalue()


def test_health() -> None:
    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_upload_pdf(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    repo = SqliteRepository(tmp_path / "test.db")
    app.dependency_overrides[get_repository] = lambda: repo
    client = TestClient(app)

    response = client.post(
        "/api/v1/documents",
        files={"file": ("example.pdf", _blank_pdf(), "application/pdf")},
    )

    app.dependency_overrides.clear()
    assert response.status_code == 201
    payload = response.json()
    assert payload["filename"] == "example.pdf"
    assert payload["page_count"] == 1
    assert payload["chunk_count"] == 0


def test_text_pdf_provenance(client, tmp_path):
    from conftest import make_pdf

    texts = ["Two reference projects are required.", "Insurance must cover EUR 1000000."]
    response = client.post(
        "/api/v1/documents", files={"file": ("tender.pdf", make_pdf(texts), "application/pdf")}
    )
    assert response.status_code == 201
    doc = response.json()
    assert doc["page_count"] == 2
    assert doc["chunk_count"] == 2
    repo = SqliteRepository(tmp_path / "test.db")
    assert repo.get_document(doc["id"]).sha256 == doc["sha256"]
    for chunk in repo.list_chunks(doc["id"]):
        page = repo.get_page(doc["id"], chunk.page_number)
        assert page.text == texts[chunk.page_number - 1]
        assert page.text[chunk.char_start : chunk.char_end] == chunk.text
    for quote, expected in [(texts[0], "VERIFIED_QUOTE"), ("Invented criterion", "INVALID_QUOTE")]:
        result = client.post(
            "/api/v1/evidence/validate",
            json={"document_id": doc["id"], "page_number": 1, "quote": quote},
        )
        assert result.json()["grounding_status"] == expected
