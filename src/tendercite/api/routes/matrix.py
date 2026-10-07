from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from tendercite.api.dependencies import get_repository
from tendercite.domain.models import AssessmentRequest, GoNoGoRow
from tendercite.repositories.sqlite import SqliteRepository

router = APIRouter(prefix="/go-no-go", tags=["decision matrix"])
Repository = Annotated[SqliteRepository, Depends(get_repository)]


@router.get("", response_model=list[GoNoGoRow])
def matrix(repository: Repository, analysis_run_id: str | None = None):
    return repository.list_matrix(analysis_run_id)


@router.patch("/{finding_id}", response_model=GoNoGoRow)
def assess(finding_id: str, request: AssessmentRequest, repository: Repository):
    try:
        return repository.set_assessment(finding_id, request)
    except KeyError as exc:
        raise HTTPException(404, "Finding not found") from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
