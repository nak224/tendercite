from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field, computed_field


class RequirementCategory(StrEnum):
    MUST = "MUST"
    SCORING = "SCORING"
    INFORMATION = "INFORMATION"
    RISK = "RISK"


class RequirementType(StrEnum):
    DEADLINE = "DEADLINE"
    ELIGIBILITY = "ELIGIBILITY"
    REFERENCE = "REFERENCE"
    FINANCIAL = "FINANCIAL"
    INSURANCE = "INSURANCE"
    TECHNICAL = "TECHNICAL"
    CERTIFICATE = "CERTIFICATE"
    EVIDENCE = "EVIDENCE"
    AWARD = "AWARD"
    PRICE = "PRICE"
    CONTRACT = "CONTRACT"
    LIABILITY = "LIABILITY"
    PRIVACY = "PRIVACY"
    SECURITY = "SECURITY"
    OTHER = "OTHER"


class ReviewStatus(StrEnum):
    UNREVIEWED = "UNREVIEWED"
    CONFIRMED = "CONFIRMED"
    MODIFIED = "MODIFIED"
    REJECTED = "REJECTED"


class AssessmentStatus(StrEnum):
    FULFILLED = "FULFILLED"
    PARTIAL = "PARTIAL"
    MISSING = "MISSING"
    CLARIFICATION_NEEDED = "CLARIFICATION_NEEDED"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class GroundingStatus(StrEnum):
    VERIFIED_QUOTE = "VERIFIED_QUOTE"
    MISSING_EVIDENCE = "MISSING_EVIDENCE"
    INVALID_QUOTE = "INVALID_QUOTE"


class DocumentRead(BaseModel):
    id: str
    filename: str
    media_type: str
    sha256: str
    page_count: int
    chunk_count: int
    created_at: datetime


class PageRead(BaseModel):
    document_id: str
    page_number: int = Field(ge=1)
    text: str


class ChunkRead(BaseModel):
    id: str
    document_id: str
    page_number: int = Field(ge=1)
    ordinal: int = Field(ge=0)
    text: str
    char_start: int = Field(ge=0)
    char_end: int = Field(ge=0)


class EvidenceRequest(BaseModel):
    document_id: str
    page_number: int = Field(ge=1)
    quote: str = Field(min_length=1, max_length=4000)


class EvidenceRef(BaseModel):
    chunk_id: str | None = None
    document_id: str
    page_number: int
    quote: str
    char_start: int | None = None
    char_end: int | None = None
    text_sha256: str | None = None
    grounding_status: GroundingStatus


class ReviewedValue(BaseModel):
    statement: str = Field(min_length=1, max_length=4000, pattern=r"\S")
    category: RequirementCategory
    requirement_type: RequirementType


class Finding(BaseModel):
    reviewed_value: ReviewedValue | None = None
    analysis_run_id: str = ""
    id: str
    statement: str
    category: RequirementCategory
    requirement_type: RequirementType = RequirementType.OTHER
    evidence: list[EvidenceRef]
    confidence: float | None = Field(default=None, ge=0, le=1)
    confidence_reason: str | None = None
    review_status: ReviewStatus = ReviewStatus.UNREVIEWED
    created_at: datetime

    @computed_field
    @property
    def effective_value(self) -> ReviewedValue:
        return self.reviewed_value or ReviewedValue(
            statement=self.statement, category=self.category, requirement_type=self.requirement_type
        )

    @computed_field
    @property
    def grounding_status(self) -> GroundingStatus:
        if not self.evidence:
            return GroundingStatus.MISSING_EVIDENCE
        if any(e.grounding_status == GroundingStatus.INVALID_QUOTE for e in self.evidence):
            return GroundingStatus.INVALID_QUOTE
        if any(e.grounding_status == GroundingStatus.MISSING_EVIDENCE for e in self.evidence):
            return GroundingStatus.MISSING_EVIDENCE
        return GroundingStatus.VERIFIED_QUOTE


class GoNoGoRow(BaseModel):
    finding_id: str
    criterion: str
    status: AssessmentStatus = AssessmentStatus.CLARIFICATION_NEEDED
    rationale: str | None = None
    evidence: list[EvidenceRef] = Field(default_factory=list)
