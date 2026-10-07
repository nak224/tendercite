from tendercite.core.config import settings
from tendercite.repositories.sqlite import SqliteRepository

_repository: SqliteRepository | None = None


def get_repository() -> SqliteRepository:
    global _repository
    if _repository is None:
        _repository = SqliteRepository(settings.database_path)
    return _repository


def get_retrieval():
    import hashlib

    from fastapi import HTTPException

    from tendercite.services.retrieval.chroma import ChromaVectorStore
    from tendercite.services.retrieval.embeddings import SentenceTransformerEmbeddingProvider
    from tendercite.services.retrieval.service import RetrievalService

    if not settings.retrieval_enabled:
        raise HTTPException(503, "Retrieval is disabled; enable TENDERCITE_RETRIEVAL_ENABLED")
    key = (str(settings.data_dir), settings.embedding_model, settings.embedding_revision)
    if key not in _retrievals:
        collection = "chunks-" + hashlib.sha256(repr(key[1:]).encode()).hexdigest()[:16]
        _retrievals[key] = RetrievalService(
            get_repository(),
            SentenceTransformerEmbeddingProvider(
                settings.embedding_model, settings.embedding_revision
            ),
            ChromaVectorStore(settings.data_dir / "chroma", collection),
        )
    return _retrievals[key]


_retrievals = {}
