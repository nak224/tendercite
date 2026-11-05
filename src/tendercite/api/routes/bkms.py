"""Opt-in official daily download, bounded local search and explicit native import."""

import threading
from typing import Annotated

from fastapi import APIRouter, Depends, Response
from pydantic import BeforeValidator

from tendercite.api.dependencies import get_read_only_repository, get_repository
from tendercite.api.routes.capability_mapping_http import MappingApiError, MappingRoute, _error
from tendercite.api.routes.opportunities import _json_contract
from tendercite.api.routes.opportunities import _operation as opportunity_operation
from tendercite.domain.bkms import (
    BkmsError,
    CatalogSummary,
    ImportInput,
    LoadInput,
    SearchInput,
    SearchResult,
)
from tendercite.domain.bkms_catalogs import (
    CatalogCollection,
    MultiSearchInput,
    MultiSearchResult,
    RemoveInput,
)
from tendercite.domain.bkms_deadlines import DeadlineLoadInput, DeadlineSummary
from tendercite.domain.bkms_reference import Digest
from tendercite.domain.opportunities import OpportunitySaveResult
from tendercite.repositories.sqlite import SqliteRepository
from tendercite.services.bkms import BkmsClient, search_catalog, search_catalogs
from tendercite.services.bkms_deadlines import parse_eforms_archive

_download_slot = threading.Lock()


class BkmsRoute(MappingRoute):
    enabled_setting = "bkms_discovery_api_enabled"
    disabled_code = "BKMS_DISCOVERY_API_DISABLED"
    invalid_input_code = "INVALID_BKMS_INPUT"
    too_large_code = "BKMS_REQUEST_TOO_LARGE"
    failure_code = "BKMS_OPERATION_FAILED"


def get_bkms_client():
    with BkmsClient() as client:
        yield client


def _operation(call):
    try:
        return opportunity_operation(call)
    except BkmsError as exc:
        status = {
            "BKMS_CATALOG_NOT_FOUND": 404,
            "BKMS_NOTICE_NOT_FOUND": 404,
            "BKMS_CATALOG_CONFLICT": 409,
            "BKMS_DEADLINE_CONFLICT": 409,
            "BKMS_STORAGE_INTEGRITY": 409,
            "BKMS_NOT_IMPORTABLE": 422,
            "BKMS_INVALID_PUBLICATION_DAY": 422,
            "BKMS_EXPORT_TOO_LARGE": 413,
            "BKMS_BUSY": 409,
            "BKMS_DAY_ALREADY_STORED": 409,
            "BKMS_DAY_LIMIT_REACHED": 409,
            "BKMS_TOTAL_STORAGE_LIMIT": 413,
            "BKMS_TIMEOUT": 504,
            "BKMS_RATE_LIMITED": 429,
        }.get(exc.code, 502)
        raise _error(status, exc.code) from None


HttpLoad = Annotated[LoadInput, BeforeValidator(_json_contract(LoadInput))]


def _search_contract(value):
    contract = (
        MultiSearchInput
        if isinstance(value, MultiSearchInput)
        or isinstance(value, dict)
        and value.get("schema_version") == "bkms-search-input-2"
        else SearchInput
    )
    return _json_contract(contract)(value)


HttpSearch = Annotated[SearchInput | MultiSearchInput, BeforeValidator(_search_contract)]
HttpImport = Annotated[ImportInput, BeforeValidator(_json_contract(ImportInput))]
HttpRemove = Annotated[RemoveInput, BeforeValidator(_json_contract(RemoveInput))]
HttpDeadlineLoad = Annotated[DeadlineLoadInput, BeforeValidator(_json_contract(DeadlineLoadInput))]
Repository = Annotated[SqliteRepository, Depends(get_repository)]
ReadRepository = Annotated[SqliteRepository, Depends(get_read_only_repository)]
router = APIRouter(
    prefix="/discovery/bkms",
    tags=["local official German procurement discovery"],
    route_class=BkmsRoute,
    responses={
        status: {"model": MappingApiError}
        for status in (400, 404, 409, 413, 415, 422, 429, 500, 502, 503, 504)
    },
)


@router.get("/catalog", response_model=CatalogSummary)
def catalog(repository: ReadRepository):
    return _operation(lambda: repository.get_bkms_catalog()[1])


@router.get("/catalogs", response_model=CatalogCollection)
def catalogs(repository: ReadRepository):
    return _operation(lambda: repository.get_bkms_catalogs()[1])


@router.post("/load", response_model=CatalogSummary)
def load(
    request: HttpLoad,
    repository: Repository,
    client: Annotated[BkmsClient, Depends(get_bkms_client)],
):
    def operation():
        if not _download_slot.acquire(blocking=False):
            raise BkmsError("BKMS_BUSY")
        try:
            repository.check_bkms_load(request)
            return repository.save_bkms_catalog(
                client.load(request), expected_catalog_id=request.expected_catalog_id
            )
        finally:
            _download_slot.release()

    return _operation(operation)


@router.post("/search", response_model=SearchResult | MultiSearchResult)
def search(request: HttpSearch, repository: ReadRepository):
    return _operation(
        lambda: (
            search_catalogs(repository, request)
            if isinstance(request, MultiSearchInput)
            else search_catalog(repository, request)
        )
    )


@router.post("/remove", response_model=CatalogCollection)
def remove(request: HttpRemove, repository: Repository):
    return _operation(lambda: repository.remove_bkms_catalog(request))


@router.get("/deadlines/{catalog_id}", response_model=DeadlineSummary)
def deadline_summary(catalog_id: Digest, repository: ReadRepository):
    return _operation(lambda: repository.get_bkms_deadline_summary(catalog_id))


@router.post("/deadlines/load", response_model=DeadlineSummary)
def load_deadlines(
    request: HttpDeadlineLoad,
    repository: Repository,
    client: Annotated[BkmsClient, Depends(get_bkms_client)],
):
    def operation():
        if not _download_slot.acquire(blocking=False):
            raise BkmsError("BKMS_BUSY")
        try:
            summary = repository.check_bkms_deadline_load(request)
            data = parse_eforms_archive(
                client.download_day(summary.export_day, "eforms.zip"),
                summary.export_day,
                summary.catalog_id,
            )
            return repository.save_bkms_deadlines(data, request)
        finally:
            _download_slot.release()

    return _operation(operation)


@router.post(
    "/import",
    response_model=OpportunitySaveResult,
    status_code=201,
    responses={200: {"model": OpportunitySaveResult}},
)
def save(request: HttpImport, response: Response, repository: Repository):
    result = _operation(lambda: repository.import_bkms_opportunity(request))
    response.status_code = 201 if result.status == "CREATED" else 200
    return result
