"""Opt-in, read-only exposure of the existing private assessment-support report."""

import json
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BeforeValidator

from tendercite.api.dependencies import get_read_only_repository
from tendercite.api.routes.capability_mapping_http import MappingApiError, MappingRoute
from tendercite.domain.capability_workflow import AssessmentReport, AssessmentSelection
from tendercite.repositories.sqlite import SqliteRepository
from tendercite.services.capability_workflow import assessment_report


class ReportRoute(MappingRoute):
    # Reports include pseudonymous individual assignments, unlike the mapping overview.
    enabled_setting = "capability_report_api_enabled"
    disabled_code = "CAPABILITY_REPORT_API_DISABLED"
    invalid_input_code = "INVALID_REPORT_INPUT"
    too_large_code = "REPORT_REQUEST_TOO_LARGE"
    failure_code = "CAPABILITY_REPORT_FAILED"


def _json_selection(value):
    if isinstance(value, AssessmentSelection):
        return value
    # Retain the strict existing contract's JSON tuple handling, without relaxing it.
    return AssessmentSelection.model_validate_json(
        json.dumps(value, ensure_ascii=False, allow_nan=False)
    )


HttpSelection = Annotated[AssessmentSelection, BeforeValidator(_json_selection)]
Repository = Annotated[SqliteRepository, Depends(get_read_only_repository)]
router = APIRouter(
    prefix="/capability-workflow",
    tags=["local capability assessment reports"],
    route_class=ReportRoute,
    responses={
        status: {"model": MappingApiError} for status in (400, 404, 409, 413, 415, 422, 500, 503)
    },
)


@router.post("/report", response_model=AssessmentReport)
def report(selection: HttpSelection, repository: Repository) -> AssessmentReport:
    """Explicit selection only; no persistence, approval, qualification or capacity inference."""
    return assessment_report(selection, repository=repository)
