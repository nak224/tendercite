from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from tendercite.api.dependencies import get_retrieval
from tendercite.services.retrieval.base import SearchHit, SearchRequest
from tendercite.services.retrieval.service import RetrievalService

router = APIRouter(tags=["retrieval"])
Retrieval = Annotated[RetrievalService, Depends(get_retrieval)]


@router.post("/search", response_model=list[SearchHit])
def search(request: SearchRequest, retrieval: Retrieval):
    try:
        return retrieval.search(request)
    except Exception as exc:
        raise HTTPException(
            503, "Retrieval unavailable; check model installation and index"
        ) from exc


@router.post("/documents/{document_id}/index")
def index_document(document_id: str, retrieval: Retrieval):
    if retrieval.repository.get_document(document_id) is None:
        raise HTTPException(404, "Document not found")
    try:
        return {"indexed_chunks": retrieval.index_document(document_id)}
    except Exception as exc:
        raise HTTPException(503, "Indexing unavailable; document retained for retry") from exc
