"""Explicit, bounded official TED metadata search and local opportunity import."""

from typing import Annotated

from fastapi import APIRouter, Depends, Response
from pydantic import BeforeValidator

from tendercite.api.dependencies import get_repository
from tendercite.api.routes.capability_mapping_http import MappingApiError, MappingRoute, _error
from tendercite.api.routes.opportunities import _json_contract
from tendercite.api.routes.opportunities import _operation as opportunity_operation
from tendercite.domain.discovery import TedImportInput, TedSearchInput, TedSearchResult
from tendercite.domain.opportunities import OpportunitySaveResult
from tendercite.repositories.sqlite import SqliteRepository
from tendercite.services.discovery import TedNotImportable, import_ted, search_ted
from tendercite.services.ted import (
    TedClient,
    TedError,
    TedNotFoundError,
    TedResponseError,
    TedTimeoutError,
)


class DiscoveryRoute(MappingRoute):
    enabled_setting = "discovery_api_enabled"
    disabled_code = "DISCOVERY_API_DISABLED"
    invalid_input_code = "INVALID_DISCOVERY_INPUT"
    too_large_code = "DISCOVERY_REQUEST_TOO_LARGE"
    failure_code = "DISCOVERY_OPERATION_FAILED"


def get_ted_client():
    # A fresh official client per explicit operation; no automatic provider retries in this UI.
    with TedClient(attempts=1) as client:
        yield client


def _operation(call):
    try:
        return opportunity_operation(call)
    except TedTimeoutError:
        raise _error(504, "TED_TIMEOUT") from None
    except TedNotFoundError:
        raise _error(404, "TED_NOTICE_NOT_FOUND") from None
    except TedResponseError:
        raise _error(502, "TED_INVALID_RESPONSE") from None
    except TedNotImportable:
        raise _error(422, "TED_NOTICE_NOT_IMPORTABLE") from None
    except TedError as exc:
        if exc.status_code == 429:
            raise _error(429, "TED_RATE_LIMITED") from None
        raise _error(502, "TED_UNAVAILABLE") from None


HttpSearch = Annotated[TedSearchInput, BeforeValidator(_json_contract(TedSearchInput))]
HttpImport = Annotated[TedImportInput, BeforeValidator(_json_contract(TedImportInput))]
Client = Annotated[TedClient, Depends(get_ted_client)]
Repository = Annotated[SqliteRepository, Depends(get_repository)]
router = APIRouter(
    prefix="/discovery/ted",
    tags=["local official TED discovery"],
    route_class=DiscoveryRoute,
    responses={
        status: {"model": MappingApiError}
        for status in (400, 404, 409, 413, 415, 422, 429, 500, 502, 503, 504)
    },
)


@router.post("/search", response_model=TedSearchResult)
def search(request: HttpSearch, client: Client) -> TedSearchResult:
    return _operation(lambda: search_ted(request, client))


@router.post(
    "/import",
    response_model=OpportunitySaveResult,
    status_code=201,
    responses={
        200: {"model": OpportunitySaveResult, "description": "Previously imported opportunity"}
    },
)
def save(
    request: HttpImport, response: Response, repository: Repository, client: Client
) -> OpportunitySaveResult:
    result = _operation(lambda: import_ted(request.publication_id, repository, client))
    response.status_code = 201 if result.status == "CREATED" else 200
    return result
