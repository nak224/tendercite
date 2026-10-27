"""Local, opt-in native profile CRUD. No snapshot, model, file or external API input."""

import json
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response
from pydantic import BeforeValidator

from tendercite.api.dependencies import get_repository
from tendercite.api.routes.capability_mapping_http import MappingApiError, MappingRoute, _error
from tendercite.domain.profiles import (
    ProfileCreate,
    ProfileError,
    ProfileErrorCode,
    ProfileListing,
    ProfileRecord,
    ProfileSaveResult,
    ProfileUpdate,
)
from tendercite.repositories.sqlite import SqliteRepository


class ProfileRoute(MappingRoute):
    enabled_setting = "profiles_api_enabled"
    disabled_code = "PROFILES_API_DISABLED"
    invalid_input_code = "INVALID_PROFILE_INPUT"
    too_large_code = "PROFILE_REQUEST_TOO_LARGE"
    failure_code = "PROFILE_OPERATION_FAILED"

    def permitted_query_parameters(self):
        return (
            {"limit", "offset"}
            if "GET" in self.methods and self.path.endswith("/profiles")
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
    except ProfileError as exc:
        status = {
            ProfileErrorCode.INVALID_INPUT: 422,
            ProfileErrorCode.NOT_FOUND: 404,
            ProfileErrorCode.ID_CONFLICT: 409,
            ProfileErrorCode.REVISION_CONFLICT: 409,
            ProfileErrorCode.STORAGE_INTEGRITY: 409,
            ProfileErrorCode.DATABASE_FAILED: 503,
        }[exc.code]
        raise _error(status, exc.code.value) from None


HttpCreate = Annotated[ProfileCreate, BeforeValidator(_json_contract(ProfileCreate))]
HttpUpdate = Annotated[ProfileUpdate, BeforeValidator(_json_contract(ProfileUpdate))]
Repository = Annotated[SqliteRepository, Depends(get_repository)]
router = APIRouter(
    prefix="/profiles",
    tags=["local native profiles"],
    route_class=ProfileRoute,
    responses={
        status: {"model": MappingApiError} for status in (400, 404, 409, 413, 415, 422, 500, 503)
    },
)


@router.post(
    "",
    response_model=ProfileSaveResult,
    status_code=201,
    responses={
        200: {"model": ProfileSaveResult, "description": "Identical creation already present"}
    },
)
def create(request: HttpCreate, response: Response, repository: Repository) -> ProfileSaveResult:
    result = _operation(lambda: repository.create_profile(request))
    response.status_code = 201 if result.status == "CREATED" else 200
    return result


@router.get("", response_model=ProfileListing)
def listing(
    repository: Repository,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0, le=1000000)] = 0,
) -> ProfileListing:
    return _operation(lambda: repository.list_profiles(limit=limit, offset=offset))


@router.get("/{profile_id}", response_model=ProfileRecord)
def retrieve(profile_id: UUID, repository: Repository) -> ProfileRecord:
    return _operation(lambda: repository.get_profile(profile_id))


@router.put("/{profile_id}", response_model=ProfileSaveResult)
def update(profile_id: UUID, request: HttpUpdate, repository: Repository) -> ProfileSaveResult:
    return _operation(lambda: repository.update_profile(profile_id, request))
