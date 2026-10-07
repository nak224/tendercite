from urllib.parse import urlsplit

from fastapi import APIRouter

from tendercite.core.config import settings

router = APIRouter(tags=["configuration"])


@router.get("/configuration")
def configuration():
    url = urlsplit(settings.llm_base_url or "")
    return {
        "llm_configured": bool(settings.llm_base_url and settings.llm_model),
        "llm_destination": url.hostname,
        "llm_model": settings.llm_model,
        "embedding_model": settings.embedding_model,
        "retrieval_enabled": settings.retrieval_enabled,
    }
