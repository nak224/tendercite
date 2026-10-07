from pathlib import Path

from tendercite.services.retrieval.base import SearchHit


class ChromaVectorStore:
    def __init__(self, path: Path, collection: str):
        import chromadb
        from chromadb.config import Settings

        self.client = chromadb.PersistentClient(
            path=str(path), settings=Settings(anonymized_telemetry=False)
        )
        self.collection = self.client.get_or_create_collection(
            collection, metadata={"hnsw:space": "cosine"}, embedding_function=None
        )

    def upsert(self, hits: list[SearchHit], vectors: list[list[float]]) -> None:
        if hits:
            self.collection.upsert(
                ids=[h.chunk_id for h in hits],
                embeddings=vectors,
                documents=[h.text for h in hits],
                metadatas=[
                    {
                        "document_id": h.document_id,
                        "page_number": h.page_number,
                        "filename": h.document_name,
                    }
                    for h in hits
                ],
            )

    def query(
        self, vector: list[float], top_k: int, document_ids: list[str] | None
    ) -> list[SearchHit]:
        if document_ids == [] or not self.collection.count():
            return []
        where = {"document_id": {"$in": document_ids}} if document_ids else None
        result = self.collection.query(
            query_embeddings=[vector],
            n_results=min(top_k, self.collection.count()),
            where=where,
            include=["metadatas", "documents", "distances"],
        )
        return [
            SearchHit(
                chunk_id=cid,
                document_id=meta["document_id"],
                document_name=meta["filename"],
                page_number=meta["page_number"],
                text=text,
                score=1.0 - distance,
            )
            for cid, meta, text, distance in zip(
                result["ids"][0],
                result["metadatas"][0],
                result["documents"][0],
                result["distances"][0],
                strict=True,
            )
        ]

    def delete_document(self, document_id: str) -> None:
        self.collection.delete(where={"document_id": document_id})
