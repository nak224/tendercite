from tendercite.repositories.sqlite import SqliteRepository
from tendercite.services.retrieval.base import (
    EmbeddingProvider,
    SearchHit,
    SearchRequest,
    VectorStore,
)


class RetrievalService:
    def __init__(
        self, repository: SqliteRepository, embeddings: EmbeddingProvider, store: VectorStore
    ):
        self.repository = repository
        self.embeddings = embeddings
        self.store = store

    def index_document(self, document_id: str) -> int:
        document = self.repository.get_document(document_id)
        if document is None:
            raise ValueError("Document not found")
        chunks = self.repository.list_chunks(document_id)
        for start in range(0, len(chunks), 64):
            batch = chunks[start : start + 64]
            hits = [
                SearchHit(
                    chunk_id=c.id,
                    document_id=document_id,
                    document_name=document.filename,
                    page_number=c.page_number,
                    text=c.text,
                    score=0,
                )
                for c in batch
            ]
            self.store.upsert(hits, self.embeddings.embed_documents([c.text for c in batch]))
        return len(chunks)

    def search(self, request: SearchRequest) -> list[SearchHit]:
        if request.document_ids == []:
            return []
        hits = self.store.query(
            self.embeddings.embed_query(request.query), request.top_k, request.document_ids
        )
        verified = []
        for hit in hits:
            chunk = self.repository.get_chunk(hit.chunk_id)
            if chunk is None:
                continue  # stale vectors can never resurrect deleted sources
            if request.document_ids is not None and chunk.document_id not in request.document_ids:
                continue
            doc = self.repository.get_document(chunk.document_id)
            if doc:
                verified.append(
                    SearchHit(
                        chunk_id=chunk.id,
                        document_id=doc.id,
                        document_name=doc.filename,
                        page_number=chunk.page_number,
                        text=chunk.text,
                        score=hit.score,
                    )
                )
        return verified
