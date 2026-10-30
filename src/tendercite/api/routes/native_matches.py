"""Opt-in, read-only native profile/opportunity label previews."""

import json
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BeforeValidator

from tendercite.api.dependencies import get_read_only_repository
from tendercite.api.routes.capability_mapping_http import MappingApiError, MappingRoute, _error
from tendercite.domain.native_matching import (
    NativeMatchError,
    NativeMatchErrorCode,
    NativeMatchResult,
    NativeMatchSelection,
)
from tendercite.repositories.sqlite import SqliteRepository
from tendercite.services.native_matching import preview_native_match


class NativeMatchRoute(MappingRoute):
    enabled_setting = "native_matching_api_enabled"
    disabled_code = "NATIVE_MATCHING_API_DISABLED"
    invalid_input_code = "INVALID_NATIVE_MATCH_INPUT"
    too_large_code = "NATIVE_MATCH_REQUEST_TOO_LARGE"
    failure_code = "NATIVE_MATCH_FAILED"


def _json_selection(value):
    return (
        value
        if isinstance(value, NativeMatchSelection)
        else NativeMatchSelection.model_validate_json(
            json.dumps(value, ensure_ascii=False, allow_nan=False)
        )
    )


HttpSelection = Annotated[NativeMatchSelection, BeforeValidator(_json_selection)]
Repository = Annotated[SqliteRepository, Depends(get_read_only_repository)]
router = APIRouter(
    prefix="/native-matches",
    tags=["local native skill-label matching"],
    route_class=NativeMatchRoute,
    responses={
        status: {"model": MappingApiError} for status in (400, 404, 409, 413, 415, 422, 500, 503)
    },
)


@router.post("/preview", response_model=NativeMatchResult)
def preview(selection: HttpSelection, repository: Repository) -> NativeMatchResult:
    try:
        return preview_native_match(selection, repository=repository)
    except NativeMatchError as exc:
        status = {
            NativeMatchErrorCode.INVALID_INPUT: 422,
            NativeMatchErrorCode.PROFILE_NOT_FOUND: 404,
            NativeMatchErrorCode.OPPORTUNITY_NOT_FOUND: 404,
            NativeMatchErrorCode.DATABASE_FAILED: 503,
        }.get(exc.code, 409)
        raise _error(status, exc.code.value) from None
