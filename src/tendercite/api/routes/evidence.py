from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from tendercite.api.dependencies import get_repository
from tendercite.domain.models import EvidenceRef, EvidenceRequest
from tendercite.repositories.sqlite import SqliteRepository
from tendercite.services.evidence import validate_evidence

router = APIRouter(prefix="/evidence", tags=["evidence"])


@router.post("/validate", response_model=EvidenceRef)
def validate_source_quote(
    request: EvidenceRequest,
    repository: Annotated[SqliteRepository, Depends(get_repository)],
) -> EvidenceRef:
    page = repository.get_page(request.document_id, request.page_number)
    if page is None:
        raise HTTPException(status_code=404, detail="Document page not found")
    return validate_evidence(
        document_id=request.document_id,
        page_number=request.page_number,
        page_text=page.text,
        quote=request.quote,
    )
