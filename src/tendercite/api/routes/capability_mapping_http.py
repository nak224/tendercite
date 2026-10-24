"""Local, opt-in HTTP access to existing manual capability mappings.

No automatic matching or qualification claims. This transport never selects a database.
"""

import json
import re
import sqlite3
from datetime import date, datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from pydantic import BaseModel, BeforeValidator, ConfigDict
from starlette.requests import ClientDisconnect

from tendercite.api.dependencies import get_repository
from tendercite.core.config import settings
from tendercite.domain.capability_mapping_records import (
    CapabilityMappingError,
    CapabilityMappingRecord,
    LocalId,
    MappingApprovalInput,
    MappingErrorCode,
)
from tendercite.domain.capability_matching import SnapshotHash
from tendercite.domain.capability_workflow import AssessmentOverview, MappingSelection
from tendercite.repositories.sqlite import SqliteRepository
from tendercite.services.capabilities import CapabilityError, CapabilityErrorCode
from tendercite.services.capability_workflow import assessment_overview, prepare_mapping_draft

MAX_MAPPING_REQUEST_BYTES = 65536
_MAX_QUERY_BYTES = 2048

_MAPPING_STATUS = {
    MappingErrorCode.INVALID_INPUT: 422,
    MappingErrorCode.NOT_FOUND: 404,
    MappingErrorCode.SNAPSHOT_NOT_FOUND: 404,
    MappingErrorCode.INVALID_SCOPE: 404,
    MappingErrorCode.UNKNOWN_SKILL: 422,
    MappingErrorCode.INELIGIBLE: 409,
    MappingErrorCode.ALREADY_EXISTS: 409,
    MappingErrorCode.CONFLICT: 409,
    MappingErrorCode.REVIEW_CONFLICT: 409,
    MappingErrorCode.INVALID_TRANSITION: 409,
    MappingErrorCode.STORAGE_INTEGRITY: 409,
    MappingErrorCode.DATABASE_FAILED: 503,
}


class _MappingHttpError(HTTPException):
    """Only fixed codes may cross this local API's privacy boundary."""


def _error(status: int, code: str) -> _MappingHttpError:
    return _MappingHttpError(
        status_code=status,
        detail={"code": code},
        headers={"Cache-Control": "no-store"},
    )


def _unique_pairs(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("Duplicate JSON object key")
        value[key] = item
    return value


def _reject_constant(_value):
    raise ValueError("Non-finite JSON number")


class MappingRoute(APIRoute):
    """Apply the flag and bounded transport checks before invoking dependencies."""

    def get_route_handler(self):
        original = super().get_route_handler()
        is_post = "POST" in self.methods
        allowed_query = (
            {"limit"}
            if self.path == "/api/v1/capability-snapshots"
            else {"analysis_id", "snapshot_hash"}
            if self.path.endswith("/overview")
            else set()
        )

        async def handle(request: Request):
            if not settings.capability_mapping_api_enabled:
                raise _error(404, "CAPABILITY_MAPPING_API_DISABLED")
            try:
                if len(request.scope.get("query_string", b"")) > _MAX_QUERY_BYTES:
                    raise _error(413, "MAPPING_REQUEST_TOO_LARGE")
                params = list(request.query_params.keys())
                if set(params) - allowed_query or any(
                    len(request.query_params.getlist(key)) != 1 for key in params
                ):
                    raise _error(422, "INVALID_MAPPING_INPUT")
                if is_post:
                    media = request.headers.getlist("content-type")
                    if (
                        len(media) != 1
                        or media[0].split(";")[0].strip().lower() != "application/json"
                    ):
                        raise _error(415, "UNSUPPORTED_MEDIA_TYPE")
                    encoding = request.headers.getlist("content-encoding")
                    if encoding and (len(encoding) != 1 or encoding[0].lower() != "identity"):
                        raise _error(415, "UNSUPPORTED_CONTENT_ENCODING")
                    lengths = request.headers.getlist("content-length")
                    if len(lengths) > 1 or (lengths and not re.fullmatch(r"[0-9]+", lengths[0])):
                        raise _error(400, "INVALID_CONTENT_LENGTH")
                    declared = None
                    if lengths:
                        digits = lengths[0].lstrip("0") or "0"
                        if len(digits) > len(str(MAX_MAPPING_REQUEST_BYTES)):
                            raise _error(413, "MAPPING_REQUEST_TOO_LARGE")
                        declared = int(digits)
                        if declared > MAX_MAPPING_REQUEST_BYTES:
                            raise _error(413, "MAPPING_REQUEST_TOO_LARGE")
                    received = 0

                    async def bounded_receive():
                        nonlocal received
                        message = await request.receive()
                        if message["type"] == "http.request":
                            received += len(message.get("body", b""))
                            if received > MAX_MAPPING_REQUEST_BYTES:
                                raise _error(413, "MAPPING_REQUEST_TOO_LARGE")
                        return message

                    bounded = Request(request.scope, receive=bounded_receive)
                    raw = await bounded.body()
                    if declared is not None and declared != received:
                        raise _error(400, "INVALID_CONTENT_LENGTH")
                    try:
                        value = json.loads(
                            raw.decode("utf-8"),
                            object_pairs_hook=_unique_pairs,
                            parse_constant=_reject_constant,
                        )
                        if not isinstance(value, dict):
                            raise ValueError("Expected a JSON object")
                    except (ValueError, UnicodeError, RecursionError, OverflowError):
                        raise _error(422, "INVALID_MAPPING_INPUT") from None
                    response = await original(bounded)
                else:
                    response = await original(request)
                response.headers["Cache-Control"] = "no-store"
                return response
            except _MappingHttpError:
                raise
            except RequestValidationError:
                raise _error(422, "INVALID_MAPPING_INPUT") from None
            except ClientDisconnect:
                raise _error(400, "INCOMPLETE_BODY") from None
            except CapabilityMappingError as exc:
                raise _error(_MAPPING_STATUS.get(exc.code, 500), exc.code.value) from None
            except CapabilityError as exc:
                status = {
                    CapabilityErrorCode.INVALID_ARGUMENTS: 422,
                    CapabilityErrorCode.STORAGE_INTEGRITY: 409,
                    CapabilityErrorCode.DATABASE_FAILED: 503,
                }.get(exc.code, 500)
                code = exc.code.value if status != 500 else "CAPABILITY_MAPPING_FAILED"
                raise _error(status, code) from None
            except (sqlite3.Error, OSError):
                raise _error(503, "DATABASE_FAILED") from None
            except Exception:
                raise _error(500, "CAPABILITY_MAPPING_FAILED") from None

        return handle


def _json_selection(value):
    if isinstance(value, MappingSelection):
        return value
    return MappingSelection.model_validate_json(
        json.dumps(value, ensure_ascii=False, allow_nan=False)
    )


def _json_approval(value):
    if isinstance(value, MappingApprovalInput):
        return value
    return MappingApprovalInput.model_validate_json(
        json.dumps(value, ensure_ascii=False, allow_nan=False)
    )


HttpSelection = Annotated[MappingSelection, BeforeValidator(_json_selection)]
HttpApproval = Annotated[MappingApprovalInput, BeforeValidator(_json_approval)]
Repository = Annotated[SqliteRepository, Depends(get_repository)]


class SnapshotMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    content_hash: SnapshotHash
    schema_version: Literal["1.0"]
    source_system: Literal["team-capacity-engine"]
    assessment_period_start: date
    assessment_period_end: date
    imported_at: datetime


class SnapshotListing(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    status: Literal["LISTED"] = "LISTED"
    snapshots: tuple[SnapshotMetadata, ...]


class MappingApiError(BaseModel):
    detail: dict[str, str]


snapshots_router = APIRouter(
    prefix="/capability-snapshots",
    tags=["local manual capability mappings"],
    route_class=MappingRoute,
)
workflow_router = APIRouter(
    prefix="/capability-workflow",
    tags=["local manual capability mappings"],
    route_class=MappingRoute,
)
mappings_router = APIRouter(
    prefix="/capability-mappings",
    tags=["local manual capability mappings"],
    route_class=MappingRoute,
)
for router in (snapshots_router, workflow_router, mappings_router):
    router.responses = {
        status: {"model": MappingApiError} for status in (400, 404, 409, 413, 415, 422, 500, 503)
    }


@snapshots_router.get("", response_model=SnapshotListing)
def list_snapshots(
    repository: Repository, limit: Annotated[int, Query(ge=1, le=100)] = 50
) -> SnapshotListing:
    """Bounded metadata only. No people, assignment details or capacity values."""
    return SnapshotListing(snapshots=tuple(repository.list_capability_snapshots(limit=limit)))


@workflow_router.get("/overview", response_model=AssessmentOverview)
def mapping_overview(
    repository: Repository, analysis_id: LocalId, snapshot_hash: SnapshotHash
) -> AssessmentOverview:
    """Existing read-consistent, explicitly scoped interpretation/mapping view."""
    return assessment_overview(
        analysis_id=analysis_id, snapshot_hash=snapshot_hash, repository=repository
    )


@mappings_router.post("/drafts", response_model=CapabilityMappingRecord, status_code=201)
def create_draft(selection: HttpSelection, repository: Repository) -> CapabilityMappingRecord:
    """Prepare current review-bound input, then validate it again inside the write transaction."""
    prepared = prepare_mapping_draft(selection, repository=repository)
    return repository.create_capability_mapping_draft(prepared)


@mappings_router.post("/{mapping_id}/approve", response_model=CapabilityMappingRecord)
def approve_draft(
    mapping_id: UUID, approval: HttpApproval, repository: Repository
) -> CapabilityMappingRecord:
    """Explicit revision- and review-bound approval, never an automatic transition."""
    if mapping_id != approval.mapping_id:
        raise _error(422, "INVALID_MAPPING_INPUT")
    return repository.approve_capability_mapping(approval)
