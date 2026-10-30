"""Local manual opportunity CRUD; no source fetching, tender analysis or matching."""

import json
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response
from pydantic import BeforeValidator

from tendercite.api.dependencies import get_repository
from tendercite.api.routes.capability_mapping_http import MappingApiError, MappingRoute, _error
from tendercite.domain.opportunities import (
    OpportunityCreate,
    OpportunityError,
    OpportunityErrorCode,
    OpportunityListing,
    OpportunityRecord,
    OpportunitySaveResult,
    OpportunityUpdate,
)
from tendercite.repositories.sqlite import SqliteRepository


class OpportunityRoute(MappingRoute):
    enabled_setting = "opportunities_api_enabled"
    disabled_code = "OPPORTUNITIES_API_DISABLED"
    invalid_input_code = "INVALID_OPPORTUNITY_INPUT"
    too_large_code = "OPPORTUNITY_REQUEST_TOO_LARGE"
    failure_code = "OPPORTUNITY_OPERATION_FAILED"

    def permitted_query_parameters(self):
        return (
            {"limit", "offset"}
            if "GET" in self.methods and self.path.endswith("/opportunities")
            else set()
        )


def _json_contract(contract):
    def validate(value):
        return (
            value
            if isinstance(value, contract)
            else contract.model_validate_json(
                json.dumps(value, ensure_ascii=False, allow_nan=False)
            )
        )

    return validate


def _operation(call):
    try:
        return call()
    except OpportunityError as exc:
        status = {
            OpportunityErrorCode.INVALID_INPUT: 422,
            OpportunityErrorCode.NOT_FOUND: 404,
            OpportunityErrorCode.ID_CONFLICT: 409,
            OpportunityErrorCode.REVISION_CONFLICT: 409,
            OpportunityErrorCode.STORAGE_INTEGRITY: 409,
            OpportunityErrorCode.DATABASE_FAILED: 503,
        }[exc.code]
        raise _error(status, exc.code.value) from None


HttpCreate = Annotated[OpportunityCreate, BeforeValidator(_json_contract(OpportunityCreate))]
HttpUpdate = Annotated[OpportunityUpdate, BeforeValidator(_json_contract(OpportunityUpdate))]
Repository = Annotated[SqliteRepository, Depends(get_repository)]
router = APIRouter(
    prefix="/opportunities",
    tags=["local native opportunities"],
    route_class=OpportunityRoute,
    responses={
        status: {"model": MappingApiError} for status in (400, 404, 409, 413, 415, 422, 500, 503)
    },
)


@router.post(
    "",
    response_model=OpportunitySaveResult,
    status_code=201,
    responses={
        200: {"model": OpportunitySaveResult, "description": "Identical creation already present"}
    },
)
def create(
    request: HttpCreate, response: Response, repository: Repository
) -> OpportunitySaveResult:
    result = _operation(lambda: repository.create_opportunity(request))
    response.status_code = 201 if result.status == "CREATED" else 200
    return result


@router.get("", response_model=OpportunityListing)
def listing(
    repository: Repository,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0, le=1000000)] = 0,
) -> OpportunityListing:
    return _operation(lambda: repository.list_opportunities(limit=limit, offset=offset))


@router.get("/{opportunity_id}", response_model=OpportunityRecord)
def retrieve(opportunity_id: UUID, repository: Repository) -> OpportunityRecord:
    return _operation(lambda: repository.get_opportunity(opportunity_id))


@router.put("/{opportunity_id}", response_model=OpportunitySaveResult)
def update(
    opportunity_id: UUID, request: HttpUpdate, repository: Repository
) -> OpportunitySaveResult:
    return _operation(lambda: repository.update_opportunity(opportunity_id, request))
