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
