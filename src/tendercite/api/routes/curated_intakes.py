"""Local HTTP transport for existing manual intake; no extraction or review decisions."""

import json
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from pydantic import BeforeValidator

from tendercite.api.dependencies import get_repository
from tendercite.domain.curated_intake import (
    MAX_INTAKE_BYTES,
    CuratedIntakeError,
    CuratedIntakeResult,
    CuratedTenderIntake,
    IntakeErrorCode,
    IntakeModel,
)
from tendercite.repositories.sqlite import SqliteRepository


def _error(status: int, code: str) -> HTTPException:
    return HTTPException(status, detail={"code": code})


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key")
        result[key] = value
    return result


def _reject_constant(value):
    raise ValueError("Non-finite JSON number")


class IntakeRoute(APIRoute):
    """Limit raw bytes and sanitize errors only on the two intake routes."""

    def get_route_handler(self):
        original = super().get_route_handler()

        async def handle(request: Request):
            if request.headers.get("content-type", "").split(";")[0].lower() != "application/json":
                raise _error(415, "UNSUPPORTED_MEDIA_TYPE")
            if request.headers.get("content-encoding", "identity").lower() != "identity":
                raise _error(415, "UNSUPPORTED_CONTENT_ENCODING")
            length = request.headers.get("content-length")
            if length is not None:
                try:
                    size = int(length)
                    if size < 0:
                        raise ValueError
                except ValueError:
                    raise _error(400, "INVALID_CONTENT_LENGTH") from None
                if size > MAX_INTAKE_BYTES:
                    raise _error(413, "INTAKE_TOO_LARGE")
            received = 0

            async def bounded_receive():
                nonlocal received
                message = await request.receive()
                if message["type"] == "http.request":
                    received += len(message.get("body", b""))
                    if received > MAX_INTAKE_BYTES:
                        raise _error(413, "INTAKE_TOO_LARGE")
                return message

            bounded = Request(request.scope, receive=bounded_receive)
            try:
                raw = await bounded.body()
                json.loads(
                    raw.decode("utf-8"),
                    object_pairs_hook=_unique_object,
                    parse_constant=_reject_constant,
                )
            except HTTPException:
                raise
            except (ValueError, UnicodeError, RecursionError):
                raise _error(422, IntakeErrorCode.INVALID_INPUT) from None
            try:
                return await original(bounded)
            except RequestValidationError as error:
                confirmation = any(
                    e["loc"] == ("body", "confirm_manual_intake") for e in error.errors()
                )
                code = (
                    IntakeErrorCode.CONFIRMATION_REQUIRED
                    if confirmation
                    else IntakeErrorCode.INVALID_INPUT
                )
                raise _error(422, code) from None
            except CuratedIntakeError as error:
                code = error.code
                status = {
                    IntakeErrorCode.INVALID_INPUT: 422,
                    IntakeErrorCode.CONFIRMATION_REQUIRED: 422,
                    IntakeErrorCode.DOCUMENT_NOT_FOUND: 404,
                    IntakeErrorCode.NOT_PDF: 409,
                    IntakeErrorCode.HASH_MISMATCH: 409,
                    IntakeErrorCode.PAGES_INCOMPLETE: 409,
                    IntakeErrorCode.PAGE_NOT_FOUND: 404,
                    IntakeErrorCode.INVALID_QUOTE: 422,
                    IntakeErrorCode.IDENTITY_CONFLICT: 409,
                    IntakeErrorCode.SOURCE_CHANGED: 409,
                }.get(code, 500)
                raise _error(status, code) from None
            except HTTPException:
                raise
            except Exception:
                # Never include provider/source content, DB paths or exception messages.
                raise _error(500, "CURATED_INTAKE_FAILED") from None

        return handle


def _json_intake(value):
    # FastAPI normally validates Python lists/strings; retain the strict contract's
    # JSON-mode tuple/enum handling instead of relaxing or copying its schema.
    if isinstance(value, CuratedTenderIntake):
        return value
    return CuratedTenderIntake.model_validate_json(
        json.dumps(value, ensure_ascii=False, allow_nan=False)
    )


HttpIntake = Annotated[CuratedTenderIntake, BeforeValidator(_json_intake)]


def _confirmed(value):
    if value is not True:
        raise ValueError("Explicit acknowledgement required")
    return value


class CuratedIntakeCommit(IntakeModel):
    intake: HttpIntake
    confirm_manual_intake: Annotated[Literal[True], BeforeValidator(_confirmed)]


class IntakeErrorDetail(IntakeModel):
    code: str


class IntakeApiError(IntakeModel):
    detail: IntakeErrorDetail


router = APIRouter(
    prefix="/curated-intakes",
    tags=["manual intake"],
    route_class=IntakeRoute,
    responses={status: {"model": IntakeApiError} for status in (400, 404, 409, 413, 415, 422, 500)},
)
Repository = Annotated[SqliteRepository, Depends(get_repository)]


@router.post("/validate", response_model=CuratedIntakeResult)
def validate(intake: HttpIntake, repository: Repository) -> CuratedIntakeResult:
    return repository.validate_curated_intake(intake)


@router.post("/commit", response_model=CuratedIntakeResult)
def commit(request: CuratedIntakeCommit, repository: Repository) -> CuratedIntakeResult:
    return repository.commit_curated_intake(request.intake)
