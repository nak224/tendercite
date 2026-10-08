import pytest
from fastapi.testclient import TestClient

from evaluation.generate import make_pdf as make_pdf
from tendercite.api.dependencies import get_repository
from tendercite.core.config import settings
from tendercite.main import app
from tendercite.repositories.sqlite import SqliteRepository


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
