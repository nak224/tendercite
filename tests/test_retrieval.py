import sys
from types import SimpleNamespace

import pytest
from conftest import make_pdf

from tendercite.api.dependencies import get_repository, get_retrieval
from tendercite.api.routes import documents
from tendercite.core.config import settings
from tendercite.main import app
from tendercite.services.retrieval.base import SearchRequest
from tendercite.services.retrieval.chroma import ChromaVectorStore
from tendercite.services.retrieval.embeddings import SentenceTransformerEmbeddingProvider
from tendercite.services.retrieval.service import RetrievalService


class FakeEmbeddings:
    model_id = "deterministic-test-only"

    def embed_documents(self, texts):
        return [self.embed_query(text) for text in texts]

    def embed_query(self, text):
        return [float("reference" in text.lower()), float("insurance" in text.lower()), 0.01]


@pytest.fixture
def retrieval(client, tmp_path, monkeypatch):
    pytest.importorskip("chromadb")
    repo = app.dependency_overrides[get_repository]()
    store = ChromaVectorStore(tmp_path / "vectors", "test-chunks")
    service = RetrievalService(repo, FakeEmbeddings(), store)
    app.dependency_overrides[get_retrieval] = lambda: service
    monkeypatch.setattr(documents, "get_retrieval", lambda: service)
    monkeypatch.setattr(settings, "retrieval_enabled", True)
    return service


def test_index_search_filter_deduplicate_and_delete(client, retrieval, tmp_path):
    def upload(name, text):
        response = client.post(
            "/api/v1/documents", files={"file": (name, make_pdf([text]), "application/pdf")}
        )
        assert response.status_code == 201, response.text
        return response.json()

    first = upload("references.pdf", "Two reference projects are required.")
    second = upload("insurance.pdf", "Insurance cover is mandatory.")
    again = upload("copy.pdf", "Two reference projects are required.")
    assert again["id"] == first["id"]
    assert retrieval.store.collection.count() == 2
    assert client.post(f"/api/v1/documents/{first['id']}/index").json()["indexed_chunks"] == 1
    assert retrieval.store.collection.count() == 2
    result = client.post("/api/v1/search", json={"query": "reference", "top_k": 1})
    hit = result.json()[0]
    assert hit["document_id"] == first["id"]
    assert hit["document_name"] == "references.pdf"
    assert hit["page_number"] == 1
    assert hit["chunk_id"] == retrieval.repository.list_chunks(first["id"])[0].id
    filtered = retrieval.search(SearchRequest(query="reference", document_ids=[second["id"]]))
    assert [h.document_id for h in filtered] == [second["id"]]
    assert retrieval.search(SearchRequest(query="reference", document_ids=[])) == []
    reopened = ChromaVectorStore(tmp_path / "vectors", "test-chunks")
    assert reopened.collection.count() == 2
    assert client.delete(f"/api/v1/documents/{first['id']}").status_code == 204
    assert retrieval.store.collection.count() == 1
    assert not (settings.upload_dir / (first["id"] + ".pdf")).exists()


def test_e5_prefixes_and_normalization(monkeypatch):
    calls = []

    class Model:
        def __init__(self, *args, **kwargs):
            assert kwargs["trust_remote_code"] is False

        def encode(self, texts, **kwargs):
            calls.append((texts, kwargs))
            return SimpleNamespace(tolist=lambda: [[1.0]] * len(texts))

    monkeypatch.setitem(
        sys.modules, "sentence_transformers", SimpleNamespace(SentenceTransformer=Model)
    )
    provider = SentenceTransformerEmbeddingProvider()
    provider.embed_documents(["source"])
    provider.embed_query("question")
    assert calls[0][0] == ["passage: source"]
    assert calls[1][0] == ["query: question"]
    assert all(kwargs["normalize_embeddings"] for _, kwargs in calls)


def test_search_validation(client):
    # Validate request models without requiring a downloaded model.
    from pydantic import ValidationError

    for data in [{"query": " "}, {"query": "x", "top_k": 0}]:
        with pytest.raises(ValidationError):
            SearchRequest(**data)
