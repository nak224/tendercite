from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from tendercite.api.dependencies import get_repository
from tendercite.domain.review import ReviewEvent, ReviewRequest
from tendercite.repositories.sqlite import SqliteRepository

router = APIRouter(tags=["reviews"])
Repository = Annotated[SqliteRepository, Depends(get_repository)]


@router.post("/findings/{finding_id}/reviews", response_model=ReviewEvent, status_code=201)
def review(finding_id: str, request: ReviewRequest, repository: Repository):
    try:
        return repository.add_review(finding_id, request)
    except KeyError as exc:
        raise HTTPException(404, "Finding not found") from exc


@router.get("/findings/{finding_id}/reviews", response_model=list[ReviewEvent])
def history(finding_id: str, repository: Repository):
    if repository.get_finding(finding_id) is None:
        raise HTTPException(404, "Finding not found")
    return repository.list_reviews(finding_id)
