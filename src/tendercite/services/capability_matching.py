"""Pure UUID membership discovery plus a thin adapter over existing repository reads."""

from collections.abc import Sequence
from enum import StrEnum

from pydantic import ValidationError

from tendercite.domain.analysis import AnalysisRun
from tendercite.domain.capabilities_v1 import CapabilitySnapshot, WorkforceMember, canonical_json
from tendercite.domain.capability_matching import (
    CandidateSkillCoverage,
    CapabilityMatchingRequest,
    CapabilityMatchingResult,
    EvidenceLimitation,
    FindingSkillCoverage,
    LimitationCode,
    NonEvaluationReason,
    RecordedSkillCoverage,
    RequirementSkillMapping,
)
from tendercite.domain.models import Finding, GroundingStatus, ReviewedValue, ReviewStatus
from tendercite.repositories.sqlite import SqliteRepository
from tendercite.services.capabilities import validate_capability_json


class MatchingErrorCode(StrEnum):
    INVALID_REQUEST = "INVALID_MATCHING_REQUEST"
    ANALYSIS_NOT_FOUND = "ANALYSIS_NOT_FOUND"
    FINDING_NOT_FOUND = "FINDING_NOT_FOUND"
    FINDING_ANALYSIS_MISMATCH = "FINDING_ANALYSIS_MISMATCH"
    SNAPSHOT_NOT_FOUND = "SNAPSHOT_NOT_FOUND"
    SNAPSHOT_HASH_MISMATCH = "SNAPSHOT_HASH_MISMATCH"
    UNKNOWN_SKILL = "UNKNOWN_CATALOG_SKILL_UUID"
    INCONSISTENT_REVIEW = "INCONSISTENT_REVIEW_STATE"


class CapabilityMatchingError(ValueError):
    def __init__(self, code: MatchingErrorCode):
        self.code = code
        super().__init__(code.value)


def _request(request: CapabilityMatchingRequest) -> CapabilityMatchingRequest:
    try:
        return CapabilityMatchingRequest.model_validate(request)
    except ValidationError:
        raise CapabilityMatchingError(MatchingErrorCode.INVALID_REQUEST) from None


def _selected_findings(request, analysis, findings):
    if analysis.id != request.analysis_id:
        raise CapabilityMatchingError(MatchingErrorCode.FINDING_ANALYSIS_MISMATCH)
    original_ids = {f.id for f in analysis.findings}
    if len(original_ids) != len(analysis.findings):
        raise CapabilityMatchingError(MatchingErrorCode.INVALID_REQUEST)
    indexed = {f.id: f for f in findings}
    if len(indexed) != len(findings) or set(indexed) - set(request.finding_ids):
        raise CapabilityMatchingError(MatchingErrorCode.INVALID_REQUEST)
    selected = []
    for finding_id in sorted(request.finding_ids):
        finding = indexed.get(finding_id)
        if finding is None:
            raise CapabilityMatchingError(MatchingErrorCode.FINDING_NOT_FOUND)
        if finding.analysis_run_id != analysis.id or finding_id not in original_ids:
            raise CapabilityMatchingError(MatchingErrorCode.FINDING_ANALYSIS_MISMATCH)
        if finding.review_status == ReviewStatus.MODIFIED and finding.reviewed_value is None:
            raise CapabilityMatchingError(MatchingErrorCode.INCONSISTENT_REVIEW)
        selected.append(finding)
    return selected


def _eligibility(finding: Finding, mapping: RequirementSkillMapping | None):
    reasons = []
    if finding.review_status == ReviewStatus.UNREVIEWED:
        reasons.append(NonEvaluationReason.UNREVIEWED)
    elif finding.review_status == ReviewStatus.REJECTED:
        reasons.append(NonEvaluationReason.REJECTED)
    elif finding.review_status not in (ReviewStatus.CONFIRMED, ReviewStatus.MODIFIED):
        raise CapabilityMatchingError(MatchingErrorCode.INCONSISTENT_REVIEW)
    if finding.grounding_status == GroundingStatus.INVALID_QUOTE:
        reasons.append(NonEvaluationReason.INVALID_QUOTE)
    elif finding.grounding_status == GroundingStatus.MISSING_EVIDENCE:
        reasons.append(NonEvaluationReason.MISSING_EVIDENCE)
    if mapping is None:
        reasons.append(NonEvaluationReason.UNMAPPED)
    return tuple(reasons)


_LIMITATIONS = (
    (
        LimitationCode.SKILLS_UNVERIFIED,
        "Internal assignments are not independent professional experience "
        "or qualification evidence.",
    ),
    (
        LimitationCode.PROFICIENCY_NOT_DURATION,
        "Proficiency is not years of experience; required experience duration is not verified.",
    ),
    (
        LimitationCode.CERTIFICATIONS_UNVERIFIED,
        "No independent certificate, reference or other qualification verification is supported.",
    ),
    (
        LimitationCode.REVIEW_NOT_FULFILLMENT,
        "Requirement review does not establish requirement fulfillment or bidder eligibility.",
    ),
    (
        LimitationCode.MISSING_NOT_ABSENCE,
        "A missing assignment means no record in this snapshot, "
        "not an absence of the person's skill.",
    ),
    (
        LimitationCode.CAPACITY_NOT_EVALUATED,
        "No capacity, daily feasibility, reservation, historical availability "
        "or commitment is assessed.",
    ),
)


def _limitations(finding: Finding) -> tuple[EvidenceLimitation, ...]:
    values = list(_LIMITATIONS)
    original = ReviewedValue(
        statement=finding.statement,
        category=finding.category,
        requirement_type=finding.requirement_type,
    )
    if finding.review_status == ReviewStatus.MODIFIED or original != finding.effective_value:
        values.append(
            (
                LimitationCode.REVIEWED_MEANING_UNVERIFIED,
                "Original quote occurrence does not verify the changed reviewed interpretation.",
            )
        )
    return tuple(EvidenceLimitation(code=code, explanation=text) for code, text in values)


def _candidate(member: WorkforceMember, mapping: RequirementSkillMapping) -> CandidateSkillCoverage:
    required = set(mapping.skill_ids)
    assigned = {s.skill_id for s in member.skills}
    matched, missing = required & assigned, required - assigned
    coverage = (
        RecordedSkillCoverage.ALL
        if not missing
        else RecordedSkillCoverage.SOME
        if matched
        else RecordedSkillCoverage.NONE
    )
    return CandidateSkillCoverage(
        person_id=member.person_id,
        engagement_type=member.engagement_type,
        required_skill_ids=tuple(sorted(required)),
        recorded_assigned_skill_ids=tuple(sorted(assigned)),
        matched_skill_ids=tuple(sorted(matched)),
        missing_assignment_ids=tuple(sorted(missing)),
        recorded_assignments=member.skills,
        coverage=coverage,
    )


def match_capabilities(
    request: CapabilityMatchingRequest,
    *,
    analysis: AnalysisRun,
    findings: Sequence[Finding],
    snapshot: CapabilitySnapshot,
) -> CapabilityMatchingResult:
    """No clock, IO, name matching, qualification inference, capacity arithmetic or mutations."""
    request = _request(request)
    # A copied/constructed model cannot bypass the existing v1 integrity validators.
    snapshot = validate_capability_json(canonical_json(snapshot))
    if snapshot.content_hash != request.snapshot_hash:
        raise CapabilityMatchingError(MatchingErrorCode.SNAPSHOT_HASH_MISMATCH)
    selected = _selected_findings(request, analysis, findings)
    catalog = {s.skill_id for s in snapshot.skill_catalog}
    mappings = {m.finding_id: m for m in request.mappings}
    if any(not set(m.skill_ids) <= catalog for m in request.mappings):
        raise CapabilityMatchingError(MatchingErrorCode.UNKNOWN_SKILL)
    rows = []
    for finding in selected:
        mapping = mappings.get(finding.id)
        reasons = _eligibility(finding, mapping)
        candidates = (
            ()
            if reasons
            else tuple(
                _candidate(member, mapping)
                for member in sorted(
                    snapshot.workforce_members, key=lambda member: member.person_id
                )
            )
        )
        # An ALL_OF set must occur on one person; never combine a possible team's skills.
        coverage = (
            RecordedSkillCoverage.NOT_EVALUATED
            if reasons
            else RecordedSkillCoverage.ALL
            if any(c.coverage == RecordedSkillCoverage.ALL for c in candidates)
            else RecordedSkillCoverage.SOME
            if any(c.coverage == RecordedSkillCoverage.SOME for c in candidates)
            else RecordedSkillCoverage.NONE
        )
        rows.append(
            FindingSkillCoverage(
                analysis_id=analysis.id,
                finding_id=finding.id,
                original_value=ReviewedValue(
                    statement=finding.statement,
                    category=finding.category,
                    requirement_type=finding.requirement_type,
                ),
                reviewed_value=finding.effective_value.model_copy(deep=True),
                review_status=finding.review_status,
                grounding_status=finding.grounding_status,
                source_references=tuple(e.model_copy(deep=True) for e in finding.evidence),
                mapping=mapping,
                coverage=coverage,
                non_evaluation_reasons=reasons,
                candidates=candidates,
                limitations=_limitations(finding),
            )
        )
    return CapabilityMatchingResult(
        request=request,
        analysis_id=analysis.id,
        snapshot_hash=snapshot.content_hash,
        assessment_period=snapshot.assessment_period,
        snapshot_source_limitations=snapshot.source_limitations,
        findings=tuple(rows),
    )


def match_from_repository(
    request: CapabilityMatchingRequest, repository: SqliteRepository
) -> CapabilityMatchingResult:
    """Use current reviewed findings, not the unreviewed findings embedded in AnalysisRun."""
    request = _request(request)
    analysis = repository.get_analysis(request.analysis_id)
    if analysis is None:
        raise CapabilityMatchingError(MatchingErrorCode.ANALYSIS_NOT_FOUND)
    findings = []
    for finding_id in request.finding_ids:
        finding = repository.get_finding(finding_id)
        if finding is None:
            raise CapabilityMatchingError(MatchingErrorCode.FINDING_NOT_FOUND)
        findings.append(finding)
    snapshot = repository.get_capability_snapshot(request.snapshot_hash)
    if snapshot is None:
        raise CapabilityMatchingError(MatchingErrorCode.SNAPSHOT_NOT_FOUND)
    return match_capabilities(request, analysis=analysis, findings=findings, snapshot=snapshot)
