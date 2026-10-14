"""A read-only adapter; matching rules and evidence guarantees remain in the 4.2a core."""

from tendercite.domain.capability_mapping_records import MappingStatus
from tendercite.domain.capability_matching import (
    CapabilityMatchingRequest,
    CapabilityMatchingResult,
    NonEvaluationReason,
    RequirementSkillMapping,
)
from tendercite.repositories.sqlite import SqliteRepository
from tendercite.services.capability_matching import match_capabilities


def match_persisted_capabilities(
    *,
    analysis_id: str,
    snapshot_hash: str,
    finding_ids: tuple[str, ...],
    repository: SqliteRepository,
) -> CapabilityMatchingResult:
    selection = CapabilityMatchingRequest(
        analysis_id=analysis_id, snapshot_hash=snapshot_hash, finding_ids=finding_ids, mappings=()
    )
    return _match_persisted_inputs(selection, repository.load_persisted_matching_inputs(selection))


def _match_persisted_inputs(selection, inputs):
    """Shared adapter logic; workflow reports use the same transaction's source state."""
    analysis, findings, snapshot, inspections = inputs
    request = CapabilityMatchingRequest(
        analysis_id=selection.analysis_id,
        snapshot_hash=selection.snapshot_hash,
        finding_ids=selection.finding_ids,
        mappings=tuple(
            RequirementSkillMapping(
                finding_id=m.finding_id, operator=m.record.operator, skill_ids=m.record.skill_ids
            )
            for m in inspections
            if m.status == MappingStatus.APPROVED
        ),
    )
    result = match_capabilities(request, analysis=analysis, findings=findings, snapshot=snapshot)
    statuses = {m.finding_id: m.status for m in inspections}
    unavailable = {
        MappingStatus.DRAFT: NonEvaluationReason.DRAFT_MAPPING,
        MappingStatus.REVOKED: NonEvaluationReason.REVOKED_MAPPING,
        MappingStatus.STALE: NonEvaluationReason.STALE_MAPPING,
    }
    rows = []
    for row in result.findings:
        reason = unavailable.get(statuses[row.finding_id])
        rows.append(
            row.model_copy(
                update={
                    "non_evaluation_reasons": row.non_evaluation_reasons
                    + ((reason,) if reason else ())
                }
            )
        )
    return result.model_copy(update={"findings": tuple(rows)})


def inspect_and_match_persisted_capabilities(*, selection, repository):
    """Compose inspection and existing matching without a second, possibly changed read.

    Empty selections have no matching result and never implicitly select all findings.
    """
    state = repository.load_capability_workflow_state(
        selection.analysis_id, selection.snapshot_hash, finding_ids=selection.finding_ids
    )
    if not selection.finding_ids:
        return state, None
    request = CapabilityMatchingRequest(
        analysis_id=selection.analysis_id,
        snapshot_hash=selection.snapshot_hash,
        finding_ids=selection.finding_ids,
        mappings=(),
    )
    return state, _match_persisted_inputs(
        request, (state.analysis, state.findings, state.snapshot, state.inspections)
    )
