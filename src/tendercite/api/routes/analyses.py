from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from tendercite.api.dependencies import get_llm, get_repository, get_retrieval
from tendercite.domain.analysis import AnalysisRequest, AnalysisRun
from tendercite.domain.models import Finding
from tendercite.repositories.sqlite import SqliteRepository
from tendercite.services.analysis import analyze
from tendercite.services.llm.base import StructuredLLM
from tendercite.services.retrieval.service import RetrievalService

router = APIRouter(tags=["analysis"])
Repository = Annotated[SqliteRepository, Depends(get_repository)]


@router.post("/analyses", response_model=AnalysisRun, status_code=201)
async def create_analysis(
    request: AnalysisRequest,
    repository: Repository,
    retrieval: Annotated[RetrievalService, Depends(get_retrieval)],
    llm: Annotated[StructuredLLM, Depends(get_llm)],
):
    if any(repository.get_document(d) is None for d in request.document_ids):
        raise HTTPException(404, "Document not found")
    try:
        return await analyze(request, repository, retrieval, llm)
    except ValueError as exc:
        # Includes malformed provider output; never expose raw source or provider bodies.
        raise HTTPException(
            422, "No usable context or invalid structured provider response"
        ) from exc
    except Exception as exc:
        raise HTTPException(
            502, "Analysis failed; check retrieval and provider configuration"
        ) from exc


@router.get("/analyses/{analysis_id}", response_model=AnalysisRun)
def get_analysis(analysis_id: str, repository: Repository):
    run = repository.get_analysis(analysis_id)
    if run is None:
        raise HTTPException(404, "Analysis not found")
    return run


@router.get("/findings", response_model=list[Finding])
def list_findings(repository: Repository, analysis_run_id: str | None = None):
    return repository.list_findings(analysis_run_id)


@router.get("/findings/{finding_id}", response_model=Finding)
def get_finding(finding_id: str, repository: Repository):
    finding = repository.get_finding(finding_id)
    if finding is None:
        raise HTTPException(404, "Finding not found")
    return finding
