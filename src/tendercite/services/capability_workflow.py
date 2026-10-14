"""Read-only orchestration of review-bound declarations and existing UUID matching."""

from tendercite.domain.capability_mapping_records import (
    CapabilityMappingError,
    MappingDraftInput,
    MappingErrorCode,
    MappingLifecycle,
    MappingStatus,
    RequirementInterpretation,
)
from tendercite.domain.capability_matching import RecordedSkillCoverage
from tendercite.domain.capability_workflow import (
    AssessedFinding,
    AssessmentOverview,
    AssessmentReport,
    AssessmentSelection,
    AssessmentSummary,
    CoverageCounts,
    FindingOverview,
    IndividualAssignmentCoverage,
    MappingOverview,
    MappingSelection,
    SnapshotOverview,
)
from tendercite.domain.models import GroundingStatus, ReviewStatus
from tendercite.services.persistent_capability_matching import (
    inspect_and_match_persisted_capabilities,
)


def _overview(state):
    findings = []
    for finding, mapping, binding in zip(
        state.findings, state.inspections, state.review_bindings, strict=True
    ):
        record = mapping.record
        eligible = (
            binding.review_event_id is not None
            and finding.review_status in (ReviewStatus.CONFIRMED, ReviewStatus.MODIFIED)
            and finding.grounding_status == GroundingStatus.VERIFIED_QUOTE
        )
        findings.append(
            FindingOverview(
                finding_id=finding.id,
                original_requirement=RequirementInterpretation(
                    statement=finding.statement,
                    category=finding.category,
                    requirement_type=finding.requirement_type,
                ),
                effective_requirement=binding.value,
                review_status=finding.review_status,
                grounding_status=finding.grounding_status,
                source_references=tuple(
                    sorted(
                        finding.evidence,
                        key=lambda e: (e.document_id, e.page_number, e.chunk_id or "", e.quote),
                    )
                ),
                current_review_binding=binding,
                mapping_preparation_eligible=eligible,
                mapping=MappingOverview(
                    status=mapping.status,
                    mapping_id=record.mapping_id if record else None,
                    revision=record.revision if record else None,
                    operator=record.operator if record else None,
                    skill_ids=record.skill_ids if record else (),
                    unavailable_reasons=mapping.reasons,
                    approved_review_binding=(
                        record.review_binding
                        if record and record.status == MappingLifecycle.APPROVED
                        else None
                    ),
                ),
            )
        )
    snapshot = state.snapshot
    return AssessmentOverview(
        analysis_id=state.analysis.id,
        snapshot_hash=snapshot.content_hash,
        analysis_finding_ids=tuple(sorted(f.id for f in state.analysis.findings)),
        snapshot=SnapshotOverview(
            content_hash=snapshot.content_hash,
            assessment_period=snapshot.assessment_period,
            skill_catalog=snapshot.skill_catalog,
            workforce_member_count=len(snapshot.workforce_members),
            source_limitations=snapshot.source_limitations,
        ),
        findings=tuple(findings),
    )


def assessment_overview(*, analysis_id, snapshot_hash, repository) -> AssessmentOverview:
    return _overview(repository.load_capability_workflow_state(analysis_id, snapshot_hash))


def prepare_mapping_draft(selection: MappingSelection, *, repository) -> MappingDraftInput:
    """Exportable input only: neither create nor approve a persisted mapping."""
    selection = MappingSelection.model_validate(selection)
    state = repository.load_capability_workflow_state(
        selection.analysis_id, selection.snapshot_hash, finding_ids=(selection.finding_id,)
    )
    finding = _overview(state).findings[0]
    if not finding.mapping_preparation_eligible:
        raise CapabilityMappingError(MappingErrorCode.INELIGIBLE)
    if finding.mapping.status != MappingStatus.NOT_FOUND:
        raise CapabilityMappingError(MappingErrorCode.ALREADY_EXISTS)
    if not set(selection.skill_ids) <= {s.skill_id for s in state.snapshot.skill_catalog}:
        raise CapabilityMappingError(MappingErrorCode.UNKNOWN_SKILL)
    return MappingDraftInput(
        **selection.model_dump(exclude={"schema_version"}),
        expected_review_event_id=finding.current_review_binding.review_event_id,
        expected_review_digest=finding.current_review_binding.digest,
    )


def assessment_report(selection: AssessmentSelection, *, repository) -> AssessmentReport:
    """No DB writes, fallback, second matching algorithm or qualification inference."""
    selection = AssessmentSelection.model_validate(selection)
    state, result = inspect_and_match_persisted_capabilities(
        selection=selection, repository=repository
    )
    overview = _overview(state)
    rows = []
    for finding, coverage in zip(overview.findings, result.findings if result else (), strict=True):
        rows.append(
            AssessedFinding(
                finding=finding,
                coverage=coverage.coverage,
                individual_counts=CoverageCounts(
                    all_required_recorded=sum(
                        c.coverage == RecordedSkillCoverage.ALL for c in coverage.candidates
                    ),
                    some_required_recorded=sum(
                        c.coverage == RecordedSkillCoverage.SOME for c in coverage.candidates
                    ),
                    no_required_recorded=sum(
                        c.coverage == RecordedSkillCoverage.NONE for c in coverage.candidates
                    ),
                ),
                individual_coverage=tuple(
                    IndividualAssignmentCoverage(
                        person_id=c.person_id,
                        matched_skill_ids=c.matched_skill_ids,
                        missing_assignment_ids=c.missing_assignment_ids,
                        coverage=c.coverage,
                    )
                    for c in coverage.candidates
                ),
                non_evaluation_reasons=coverage.non_evaluation_reasons,
                limitations=coverage.limitations,
            )
        )
    statuses = [f.mapping.status for f in overview.findings]
    return AssessmentReport(
        analysis_id=overview.analysis_id,
        snapshot_hash=overview.snapshot_hash,
        snapshot=overview.snapshot,
        summary=AssessmentSummary(
            selected_finding_count=len(rows),
            current_approved=statuses.count(MappingStatus.APPROVED),
            drafts=statuses.count(MappingStatus.DRAFT),
            stale=statuses.count(MappingStatus.STALE),
            revoked=statuses.count(MappingStatus.REVOKED),
            without_mapping=statuses.count(MappingStatus.NOT_FOUND),
            not_eligible_for_matching=sum(
                r.coverage == RecordedSkillCoverage.NOT_EVALUATED for r in rows
            ),
        ),
        findings=tuple(rows),
    )
