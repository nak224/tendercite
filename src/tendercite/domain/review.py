from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from tendercite.domain.models import ReviewedValue, ReviewStatus


class ReviewAction(StrEnum):
    CONFIRM = "CONFIRM"
    MODIFY = "MODIFY"
    REJECT = "REJECT"


class ReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: ReviewAction
    reviewed_value: ReviewedValue | None = None
    comment: str = Field(default="", max_length=4000)

    @model_validator(mode="after")
    def check_modification(self):
        if (self.action == ReviewAction.MODIFY) != (self.reviewed_value is not None):
            raise ValueError("Only MODIFY requires a complete reviewed_value")
        return self


class ReviewEvent(BaseModel):
    id: str
    finding_id: str
    action: ReviewAction
    original_value: dict
    previous_value: ReviewedValue
    reviewed_value: ReviewedValue
    previous_status: ReviewStatus
    comment: str
    created_at: datetime
