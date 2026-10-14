"""Synthetic reviewed findings; original pinned workforce fixture remains unchanged."""

from datetime import UTC, datetime
from uuid import UUID

from capability_helpers import FIXTURE, encode

from tendercite.domain.analysis import AnalysisRequest, AnalysisRun
from tendercite.domain.capability_matching import CapabilityMatchingRequest, RequirementSkillMapping
from tendercite.domain.models import (
    EvidenceRef,
    Finding,
    GroundingStatus,
    RequirementCategory,
    RequirementType,
    ReviewStatus,
)
from tendercite.services.capabilities import validate_capability_json

SKILL_A, SKILL_B = UUID(int=100), UUID(int=101)


def snapshot(body=None):
    return validate_capability_json(FIXTURE.read_bytes() if body is None else encode(body))


def finding(finding_id="finding-1", analysis_id="analysis-1", **changes):
    values = dict(
        id=finding_id,
        analysis_run_id=analysis_id,
        statement="Experience with PostgreSQL",
        category=RequirementCategory.MUST,
        requirement_type=RequirementType.TECHNICAL,
        review_status=ReviewStatus.CONFIRMED,
        created_at=datetime(2027, 2, 1, tzinfo=UTC),
        evidence=[
            EvidenceRef(
                document_id="synthetic-document",
                chunk_id="synthetic-chunk",
                page_number=1,
                quote="Experience with PostgreSQL",
                char_start=0,
                char_end=26,
                text_sha256="a" * 64,
                grounding_status=GroundingStatus.VERIFIED_QUOTE,
            )
        ],
    )
    return Finding(**(values | changes))


def analysis(*findings, analysis_id="analysis-1"):
    return AnalysisRun(
        id=analysis_id,
        provider="synthetic",
        model="unused",
        embedding_model="unused",
        prompt_version="synthetic-1",
        schema_version="synthetic-1",
        request=AnalysisRequest(document_ids=["synthetic-document"]),
        retrieved_chunk_ids=["synthetic-chunk"],
        created_at=datetime(2027, 2, 1, tzinfo=UTC),
        findings=list(findings),
    )


def request(
    snapshot,
    *,
    finding_ids=("finding-1",),
    skills=(SKILL_B,),
    mappings=None,
    analysis_id="analysis-1",
):
    return CapabilityMatchingRequest(
        analysis_id=analysis_id,
        snapshot_hash=snapshot.content_hash,
        finding_ids=finding_ids,
        mappings=tuple(
            RequirementSkillMapping(finding_id=i, operator="ALL_OF", skill_ids=skills)
            for i in finding_ids
        )
        if mappings is None
        else mappings,
    )
