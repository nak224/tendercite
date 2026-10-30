"""Local manual pipeline CRUD. Stages never invoke external actions."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response
from pydantic import BeforeValidator

from tendercite.api.dependencies import get_repository
from tendercite.api.routes.capability_mapping_http import MappingApiError, MappingRoute, _error
from tendercite.api.routes.opportunities import _json_contract
from tendercite.domain.pipeline import (
    PipelineCreate,
    PipelineEntry,
    PipelineError,
    PipelineErrorCode,
    PipelineListing,
    PipelineSaveResult,
    PipelineStage,
    PipelineUpdate,
)
from tendercite.repositories.sqlite import SqliteRepository


class PipelineRoute(MappingRoute):
    enabled_setting = "pipeline_api_enabled"
    disabled_code = "PIPELINE_API_DISABLED"
    invalid_input_code = "INVALID_PIPELINE_INPUT"
    too_large_code = "PIPELINE_REQUEST_TOO_LARGE"
    failure_code = "PIPELINE_OPERATION_FAILED"

    def permitted_query_parameters(self):
        return (
            {"limit", "offset", "stage", "opportunity_id"}
            if ("GET" in self.methods and self.path.endswith("/pipeline"))
            else set()
        )


def _operation(call):
    try:
        return call()
    except PipelineError as exc:
        status = {
            PipelineErrorCode.INVALID_INPUT: 422,
            PipelineErrorCode.NOT_FOUND: 404,
            PipelineErrorCode.OPPORTUNITY_NOT_FOUND: 404,
            PipelineErrorCode.DATABASE_FAILED: 503,
        }.get(exc.code, 409)
        raise _error(status, exc.code.value) from None


HttpCreate = Annotated[PipelineCreate, BeforeValidator(_json_contract(PipelineCreate))]
HttpUpdate = Annotated[PipelineUpdate, BeforeValidator(_json_contract(PipelineUpdate))]
Repository = Annotated[SqliteRepository, Depends(get_repository)]
router = APIRouter(
    prefix="/pipeline",
    tags=["local manual opportunity pipeline"],
    route_class=PipelineRoute,
    responses={
        status: {"model": MappingApiError} for status in (400, 404, 409, 413, 415, 422, 500, 503)
    },
)


@router.post(
    "",
    response_model=PipelineSaveResult,
    status_code=201,
    responses={200: {"model": PipelineSaveResult, "description": "Opportunity already tracked"}},
)
def create(request: HttpCreate, response: Response, repository: Repository) -> PipelineSaveResult:
    result = _operation(lambda: repository.create_pipeline_entry(request))
    response.status_code = 201 if result.status == "CREATED" else 200
    return result


@router.get("", response_model=PipelineListing)
def listing(
    repository: Repository,
    stage: PipelineStage | None = None,
    opportunity_id: UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0, le=1000000)] = 0,
) -> PipelineListing:
    return _operation(
        lambda: repository.list_pipeline(
            stage=stage, opportunity_id=opportunity_id, limit=limit, offset=offset
        )
    )


@router.get("/{pipeline_id}", response_model=PipelineEntry)
def retrieve(pipeline_id: UUID, repository: Repository) -> PipelineEntry:
    return _operation(lambda: repository.get_pipeline_entry(pipeline_id))


@router.put("/{pipeline_id}", response_model=PipelineSaveResult)
def update(pipeline_id: UUID, request: HttpUpdate, repository: Repository) -> PipelineSaveResult:
    return _operation(lambda: repository.update_pipeline_entry(pipeline_id, request))
