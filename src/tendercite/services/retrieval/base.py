from typing import Protocol

from pydantic import BaseModel, Field


class SearchHit(BaseModel):
    chunk_id: str
    document_id: str
    document_name: str
    page_number: int
    text: str
    score: float


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=2000, pattern=r"\S")
    document_ids: list[str] | None = Field(default=None, max_length=100)
    top_k: int = Field(default=5, ge=1, le=50)


class EmbeddingProvider(Protocol):
    model_id: str

    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...
    def embed_query(self, text: str) -> list[float]: ...


class VectorStore(Protocol):
    def upsert(self, hits: list[SearchHit], vectors: list[list[float]]) -> None: ...
    def query(
        self, vector: list[float], top_k: int, document_ids: list[str] | None
    ) -> list[SearchHit]: ...
    def delete_document(self, document_id: str) -> None: ...
