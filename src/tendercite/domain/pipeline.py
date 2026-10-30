"""Manual acquisition tracking; stages are declarations, never verified external outcomes."""

from datetime import date
from enum import StrEnum
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import AwareDatetime, Field, StringConstraints, model_validator

from tendercite.domain.opportunities import OpportunityMetadata, OpportunityModel, Revision


class PipelineStage(StrEnum):
    SAVED = "SAVED"
    REVIEWING = "REVIEWING"
    PREPARING = "PREPARING"
    SUBMITTED = "SUBMITTED"
    WON = "WON"
    LOST = "LOST"
    ARCHIVED = "ARCHIVED"


class PipelinePriority(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class PipelineValues(OpportunityModel):
    stage: PipelineStage = PipelineStage.SAVED
    priority: PipelinePriority = PipelinePriority.MEDIUM
    notes: Annotated[str, StringConstraints(max_length=4000)] = ""
    next_action: Annotated[str, StringConstraints(max_length=1000)] = ""
    next_action_due_date: date | None = None


class PipelineCreate(OpportunityModel):
    schema_version: Literal["native-pipeline-create-1"] = "native-pipeline-create-1"
    pipeline_id: UUID
    opportunity_id: UUID


class PipelineUpdate(OpportunityModel):
    schema_version: Literal["native-pipeline-update-1"] = "native-pipeline-update-1"
    expected_revision: Revision
    values: PipelineValues


class PipelineRecord(OpportunityModel):
    schema_version: Literal["native-pipeline-record-1"] = "native-pipeline-record-1"
    pipeline_id: UUID
    opportunity_id: UUID
    revision: Revision
    created_at: AwareDatetime
    updated_at: AwareDatetime
    values: PipelineValues

    @model_validator(mode="after")
    def timestamp_order(self) -> Self:
        if self.updated_at < self.created_at:
            raise ValueError("Invalid record timestamps")
        return self


class PipelineEntry(OpportunityModel):
    schema_version: Literal["native-pipeline-entry-1"] = "native-pipeline-entry-1"
    record: PipelineRecord
    opportunity: OpportunityMetadata

    @model_validator(mode="after")
    def same_opportunity(self) -> Self:
        if self.record.opportunity_id != self.opportunity.opportunity_id:
            raise ValueError("Invalid record association")
        return self


class PipelineListing(OpportunityModel):
    schema_version: Literal["native-pipeline-listing-1"] = "native-pipeline-listing-1"
    entries: Annotated[tuple[PipelineEntry, ...], Field(max_length=100)]
    next_offset: Annotated[int, Field(ge=0)] | None


class PipelineSaveResult(OpportunityModel):
    schema_version: Literal["native-pipeline-save-result-1"] = "native-pipeline-save-result-1"
    status: Literal["CREATED", "ALREADY_PRESENT", "UPDATED"]
    entry: PipelineEntry


class PipelineErrorCode(StrEnum):
    INVALID_INPUT = "INVALID_PIPELINE_INPUT"
    NOT_FOUND = "PIPELINE_NOT_FOUND"
    OPPORTUNITY_NOT_FOUND = "OPPORTUNITY_NOT_FOUND"
    ID_CONFLICT = "PIPELINE_ID_CONFLICT"
    REVISION_CONFLICT = "PIPELINE_REVISION_CONFLICT"
    STORAGE_INTEGRITY = "PIPELINE_STORAGE_INTEGRITY"
    OPPORTUNITY_INTEGRITY = "OPPORTUNITY_STORAGE_INTEGRITY"
    DATABASE_FAILED = "DATABASE_FAILED"


class PipelineError(ValueError):
    def __init__(self, code: PipelineErrorCode):
        self.code = code
        super().__init__(code.value)
