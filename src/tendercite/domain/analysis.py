from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from tendercite.domain.models import Finding, RequirementCategory, RequirementType


class CandidateEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")
    chunk_id: str
    document_id: str
    page_number: int = Field(ge=1)
    quote: str = Field(max_length=4000)


class CandidateFinding(BaseModel):
    model_config = ConfigDict(extra="forbid")
    statement: str = Field(min_length=1, max_length=4000)
    category: RequirementCategory
    requirement_type: RequirementType = RequirementType.OTHER
    confidence: float | None = Field(default=None, ge=0, le=1)
    evidence: list[CandidateEvidence] = Field(default_factory=list, max_length=20)


class ExtractionResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    findings: list[CandidateFinding] = Field(max_length=100)


class AnalysisRequest(BaseModel):
    document_ids: list[str] = Field(min_length=1, max_length=100)
    query: str = Field(
        default="Mandatory requirements, deadlines, references, insurance, "
        "certificates, award criteria, pricing, security and privacy requirements",
        min_length=1,
        max_length=2000,
        pattern=r"\S",
    )
    top_k: int = Field(
        default=12,
        ge=1,
        le=50,
        description="Maximum unique context chunks; the retrieval plan may impose a lower cap.",
    )


class RetrievalQuery(BaseModel):
    model_config = ConfigDict(frozen=True)
    key: str
    query: str


class AnalysisRetrievalPlan(BaseModel):
    model_config = ConfigDict(frozen=True)
    strategy: str
    version: str
    queries: tuple[RetrievalQuery, ...]
    hits_per_query: int
    max_chunks: int


class RetrievalQueryResult(BaseModel):
    query_key: str
    chunk_ids: list[str]


class AnalysisRun(BaseModel):
    id: str
    provider: str
    model: str
    embedding_model: str
    embedding_revision: str | None = None
    prompt_version: str
    schema_version: str
    request: AnalysisRequest
    # Old snapshots predate explicit plans; do not label them as category-aware.
    retrieval_plan: AnalysisRetrievalPlan | None = None
    retrieval_query_results: list[RetrievalQueryResult] = Field(default_factory=list)
    retrieved_chunk_ids: list[str]
    created_at: datetime
    findings: list[Finding]
