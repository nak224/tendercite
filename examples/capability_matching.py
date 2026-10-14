"""Synthetic, offline demonstration: no DB writes, provider calls or qualification claims."""

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from tendercite.domain.analysis import AnalysisRequest, AnalysisRun
from tendercite.domain.capability_matching import CapabilityMatchingRequest, RequirementSkillMapping
from tendercite.domain.models import EvidenceRef, Finding
from tendercite.services.capabilities import read_capability_snapshot
from tendercite.services.capability_matching import match_capabilities


def example_result():
    fixture = (
        Path(__file__).resolve().parents[1]
        / "tests/fixtures/capabilities/tendercite-capability-v1.json"
    )
    snapshot = read_capability_snapshot(fixture)
    quote = "At least three years of PostgreSQL development experience are required."
    finding = Finding(
        id="synthetic-finding",
        analysis_run_id="synthetic-analysis",
        statement=quote,
        category="MUST",
        requirement_type="TECHNICAL",
        review_status="CONFIRMED",
        created_at=datetime(2027, 2, 1, tzinfo=UTC),
        # Synthetic input represents an already reviewed/grounded finding. This example
        # does not perform source or human review and provides no qualification evidence.
        evidence=[
            EvidenceRef(
                document_id="synthetic-document",
                chunk_id="synthetic-chunk",
                page_number=1,
                quote=quote,
                char_start=0,
                char_end=len(quote),
                text_sha256=hashlib.sha256(quote.encode()).hexdigest(),
                grounding_status="VERIFIED_QUOTE",
            )
        ],
    )
    analysis = AnalysisRun(
        id=finding.analysis_run_id,
        provider="synthetic",
        model="unused",
        embedding_model="unused",
        prompt_version="synthetic",
        schema_version="synthetic",
        request=AnalysisRequest(document_ids=["synthetic-document"]),
        retrieved_chunk_ids=["synthetic-chunk"],
        created_at=finding.created_at,
        findings=[finding],
    )
    request = CapabilityMatchingRequest(
        analysis_id=analysis.id,
        snapshot_hash=snapshot.content_hash,
        finding_ids=(finding.id,),
        mappings=(
            RequirementSkillMapping(
                finding_id=finding.id,
                operator="ALL_OF",
                # Explicit operator choice from the pinned fixture; never looked up by name.
                skill_ids=(UUID("00000000-0000-0000-0000-000000000065"),),
            ),
        ),
    )
    return match_capabilities(request, analysis=analysis, findings=[finding], snapshot=snapshot)


if __name__ == "__main__":
    result = example_result()
    row = result.findings[0]
    print(
        json.dumps(
            {
                "snapshot_hash": result.snapshot_hash,
                "individual_coverage": [c.coverage.value for c in row.candidates],
                "qualification": row.qualification,
                "capacity_evaluation": row.capacity_evaluation,
            },
            sort_keys=True,
        )
    )
