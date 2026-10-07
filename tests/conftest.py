from io import BytesIO

import pytest
from fastapi.testclient import TestClient
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from tendercite.api.dependencies import get_repository
from tendercite.core.config import settings
from tendercite.main import app
from tendercite.repositories.sqlite import SqliteRepository


def make_pdf(pages):
    writer = PdfWriter()
    for text in pages:
        page = writer.add_blank_page(width=612, height=792)
        font = DictionaryObject(
            {
                NameObject("/Type"): NameObject("/Font"),
                NameObject("/Subtype"): NameObject("/Type1"),
                NameObject("/BaseFont"): NameObject("/Helvetica"),
            }
        )
        page[NameObject("/Resources")] = DictionaryObject(
            {NameObject("/Font"): DictionaryObject({NameObject("/F1"): writer._add_object(font)})}
        )
        stream = DecodedStreamObject()
        escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        stream.set_data(f"BT /F1 12 Tf 50 700 Td ({escaped}) Tj ET".encode("latin-1"))
        page[NameObject("/Contents")] = writer._add_object(stream)
    buf = BytesIO()
    writer.write(buf)
    return buf.getvalue()


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "retrieval_enabled", False)
    repo = SqliteRepository(tmp_path / "test.db")
    app.dependency_overrides[get_repository] = lambda: repo
    try:
        with TestClient(app) as client:
            yield client
    finally:
        app.dependency_overrides.clear()


@pytest.fixture
def retrieval(client, tmp_path, monkeypatch):
    from test_retrieval import FakeEmbeddings

    from tendercite.api.dependencies import get_retrieval
    from tendercite.api.routes import documents
    from tendercite.services.retrieval.chroma import ChromaVectorStore
    from tendercite.services.retrieval.service import RetrievalService

    pytest.importorskip("chromadb")
    repo = app.dependency_overrides[get_repository]()
    store = ChromaVectorStore(tmp_path / "vectors", "test-chunks")
    service = RetrievalService(repo, FakeEmbeddings(), store)
    app.dependency_overrides[get_retrieval] = lambda: service
    monkeypatch.setattr(documents, "get_retrieval", lambda: service)
    monkeypatch.setattr(settings, "retrieval_enabled", True)
    return service
