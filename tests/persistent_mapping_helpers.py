"""Original synthetic producer fixture and explicitly human-reviewed synthetic findings."""

from matching_helpers import SKILL_B, analysis, finding, snapshot

from tendercite.domain.capability_mapping_records import (
    MappingApprovalInput,
    MappingDraftInput,
    MappingRevisionInput,
    MappingRevocationInput,
)
from tendercite.domain.models import PageRead, ReviewStatus
from tendercite.domain.review import ReviewRequest
from tendercite.repositories.sqlite import SqliteRepository


def seeded_repository(path, *, confirm=True, findings=None):
    repo = SqliteRepository(path)
    values = findings or [finding(review_status=ReviewStatus.UNREVIEWED)]
    repo.save_document(
        document_id="synthetic-document",
        filename="synthetic.pdf",
        media_type="application/pdf",
        sha256="b" * 64,
        pages=[PageRead(document_id="synthetic-document", page_number=1, text=values[0].statement)],
        chunks=[],
    )
    repo.save_analysis(analysis(*values))
    repo.import_capability_snapshot(snapshot())
    if confirm:
        for value in values:
            repo.add_review(value.id, ReviewRequest(action="CONFIRM"))
    return repo


def draft_input(repo, **changes):
    identity = dict(
        analysis_id="analysis-1", finding_id="finding-1", snapshot_hash=snapshot().content_hash
    )
    identity.update({k: changes[k] for k in identity if k in changes})
    binding = repo.get_mapping_review_binding(**identity)
    values = dict(
        **identity,
        operator="ALL_OF",
        skill_ids=(SKILL_B,),
        expected_review_event_id=binding.review_event_id,
        expected_review_digest=binding.digest,
        rationale="Manual synthetic PostgreSQL association",
        operator_alias="operator_a",
    )
    return MappingDraftInput(**(values | changes))


def approval_input(record, **changes):
    return MappingApprovalInput(
        **(
            dict(
                mapping_id=record.mapping_id,
                expected_revision=record.revision,
                expected_review_event_id=record.review_binding.review_event_id,
                expected_review_digest=record.review_binding.digest,
                rationale="Explicit operator approval",
                operator_alias="operator_a",
            )
            | changes
        )
    )


def revision_input(repo, record, **changes):
    binding = repo.get_mapping_review_binding(
        record.analysis_id, record.finding_id, record.snapshot_hash
    )
    return MappingRevisionInput(
        **(
            dict(
                mapping_id=record.mapping_id,
                expected_revision=record.revision,
                operator="ALL_OF",
                skill_ids=record.skill_ids,
                expected_review_event_id=binding.review_event_id,
                expected_review_digest=binding.digest,
                rationale="Reconsidered against current review",
            )
            | changes
        )
    )


def revocation_input(record, **changes):
    return MappingRevocationInput(
        **(
            dict(
                mapping_id=record.mapping_id,
                expected_revision=record.revision,
                rationale="Operator withdraws mapping",
            )
            | changes
        )
    )


def approved_mapping(repo):
    draft = repo.create_capability_mapping_draft(draft_input(repo))
    return repo.approve_capability_mapping(approval_input(draft))
