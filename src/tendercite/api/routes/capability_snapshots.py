"""Bounded local HTTP transport for the existing capability snapshot importer."""

import json
import re
import sqlite3
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from pydantic import BaseModel, BeforeValidator, ConfigDict, Field
from starlette.requests import ClientDisconnect

from tendercite.api.dependencies import get_repository
from tendercite.core.config import settings
from tendercite.domain.capabilities_v1 import AssessmentPeriod, CapabilitySnapshot
from tendercite.repositories.sqlite import SqliteRepository
from tendercite.services.capabilities import (
    MAX_SNAPSHOT_BYTES,
    CapabilityError,
    CapabilityErrorCode,
    snapshot_summary,
    validate_capability_json,
)

ACKNOWLEDGEMENT_HEADER = "X-TenderCite-Acknowledge-Local-Synthetic-Data"
_ERROR_STATUS = {
    CapabilityErrorCode.INPUT_TOO_LARGE: 413,
    CapabilityErrorCode.INVALID_JSON: 422,
    CapabilityErrorCode.UNSUPPORTED_SCHEMA: 422,
    CapabilityErrorCode.INVALID_CONTRACT: 422,
    CapabilityErrorCode.HASH_MISMATCH: 422,
    CapabilityErrorCode.STORAGE_INTEGRITY: 409,
    CapabilityErrorCode.DATABASE_FAILED: 503,
}


class _SnapshotHttpError(HTTPException):
    """Only fixed errors created by this transport may cross its privacy boundary."""


def _error(status: int, code: str) -> HTTPException:
    return _SnapshotHttpError(status, detail={"code": code}, headers={"Cache-Control": "no-store"})


class SnapshotRoute(APIRoute):
    """Guard before dependencies run, retaining raw-byte duplicate-key validation."""

    def get_route_handler(self):
        original = super().get_route_handler()
        importing = self.path.endswith("/import")

        async def handle(request: Request):
            if not settings.capability_snapshot_api_enabled:
                raise _error(404, "CAPABILITY_SNAPSHOT_API_DISABLED")
            media = request.headers.getlist("content-type")
            if len(media) != 1 or media[0].split(";")[0].strip().lower() != "application/json":
                raise _error(415, "UNSUPPORTED_MEDIA_TYPE")
            encodings = request.headers.getlist("content-encoding")
            if encodings and (len(encodings) != 1 or encodings[0].lower() != "identity"):
                raise _error(415, "UNSUPPORTED_CONTENT_ENCODING")
            if importing and request.headers.getlist(ACKNOWLEDGEMENT_HEADER) != ["true"]:
                raise _error(422, "ACKNOWLEDGEMENT_REQUIRED")
            lengths = request.headers.getlist("content-length")
            if len(lengths) > 1 or (lengths and not re.fullmatch(r"[0-9]+", lengths[0])):
                raise _error(400, "INVALID_CONTENT_LENGTH")
            declared = None
            if lengths:
                # Bound integer parsing too: an arbitrary-length header must not escape as 500.
                digits = lengths[0].lstrip("0") or "0"
                if len(digits) > len(str(MAX_SNAPSHOT_BYTES)):
                    raise _error(413, "INPUT_TOO_LARGE")
                declared = int(digits)
                if declared > MAX_SNAPSHOT_BYTES:
                    raise _error(413, "INPUT_TOO_LARGE")
            received = 0

            async def bounded_receive():
                nonlocal received
                message = await request.receive()
                if message["type"] == "http.request":
                    received += len(message.get("body", b""))
                    if received > MAX_SNAPSHOT_BYTES:
                        raise _error(413, "INPUT_TOO_LARGE")
                return message

            bounded = Request(request.scope, receive=bounded_receive)
            try:
                raw = await bounded.body()
                if declared is not None and declared != received:
                    raise _error(400, "INVALID_CONTENT_LENGTH")
                validate_capability_json(raw)
                response = await original(bounded)
                response.headers["Cache-Control"] = "no-store"
                return response
            except CapabilityError as error:
                if error.code in _ERROR_STATUS:
                    raise _error(_ERROR_STATUS[error.code], error.code.value) from None
                raise _error(500, "CAPABILITY_SNAPSHOT_FAILED") from None
            except RequestValidationError:
                raise _error(422, "INVALID_CONTRACT") from None
            except ClientDisconnect:
                raise _error(400, "INCOMPLETE_BODY") from None
            except (sqlite3.Error, OSError):
                raise _error(503, "DATABASE_FAILED") from None
            except _SnapshotHttpError:
                raise
            except Exception:
                raise _error(500, "CAPABILITY_SNAPSHOT_FAILED") from None

        return handle


def _json_snapshot(value):
    if isinstance(value, CapabilitySnapshot):
        return value
    # Use the same validator in JSON mode for FastAPI's parsed dict as for raw bytes.
    return validate_capability_json(
        json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode(
            "utf-8"
        )
    )


HttpSnapshot = Annotated[CapabilitySnapshot, BeforeValidator(_json_snapshot)]
Repository = Annotated[SqliteRepository, Depends(get_repository)]


class SnapshotResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    status: Literal["VALIDATED", "INSERTED", "ALREADY_PRESENT"]
    content_hash: str
    schema_version: Literal["1.0"]
    source_system: Literal["team-capacity-engine"]
    assessment_period: AssessmentPeriod
    member_count: int = Field(ge=0)
    assignment_count: int = Field(ge=0)
    catalog_count: int = Field(ge=0)
    imported_at: datetime | None = None


class SnapshotErrorDetail(BaseModel):
    code: str


class SnapshotApiError(BaseModel):
    detail: SnapshotErrorDetail


router = APIRouter(
    prefix="/capability-snapshots",
    tags=["local capability snapshots"],
    route_class=SnapshotRoute,
    responses={
        status: {"model": SnapshotApiError} for status in (400, 404, 409, 413, 415, 422, 500, 503)
    },
)


@router.post("/validate", response_model=SnapshotResult)
def validate(snapshot: HttpSnapshot) -> SnapshotResult:
    """Validate raw schema 1.0 JSON; no repository dependency or persistence."""
    return SnapshotResult(status="VALIDATED", **snapshot_summary(snapshot))


@router.post("/import", response_model=SnapshotResult)
def import_snapshot(
    snapshot: HttpSnapshot,
    repository: Repository,
    acknowledgement: Annotated[Literal["true"], Header(alias=ACKNOWLEDGEMENT_HEADER)],
) -> SnapshotResult:
    """Explicit synthetic-local import; acknowledgement does not verify data provenance."""
    result = repository.import_capability_snapshot(snapshot)
    return SnapshotResult(
        status="INSERTED" if result.inserted else "ALREADY_PRESENT",
        imported_at=result.metadata.imported_at,
        **snapshot_summary(snapshot),
    )
