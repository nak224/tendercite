"""Deterministic comparison of saved native declarations, not professional qualification."""

from tendercite.domain.native_matching import (
    ComparedOpportunity,
    ComparedProfile,
    NativeMatchCounts,
    NativeMatchResult,
    NativeMatchSelection,
    ProfileLabel,
    RequiredLabel,
)
from tendercite.domain.profiles import normalized_label


def preview_native_match(selection: NativeMatchSelection, *, repository) -> NativeMatchResult:
    profile, opportunity = repository.load_native_match_records(selection)
    labels = tuple(
        ProfileLabel(
            skill_id=s.skill_id, original_label=s.name, normalized_label=normalized_label(s.name)
        )
        for s in profile.profile.skills
    )
    by_label = {s.normalized_label: s.skill_id for s in labels}
    rows = []
    for original in opportunity.opportunity.required_skills:
        normalized = normalized_label(original)
        skill_id = by_label.get(normalized)
        rows.append(
            RequiredLabel(
                original_label=original,
                normalized_label=normalized,
                status="MATCHED" if skill_id is not None else "NOT_RECORDED",
                matched_profile_skill_ids=(skill_id,) if skill_id is not None else (),
            )
        )
    matched = sum(row.status == "MATCHED" for row in rows)
    return NativeMatchResult(
        status="COMPARED" if rows else "NO_REQUIREMENTS",
        profile=ComparedProfile(
            profile_id=profile.profile_id,
            revision=profile.revision,
            name=profile.profile.name,
            profile_type=profile.profile.profile_type,
            skills=labels,
        ),
        opportunity=ComparedOpportunity(
            opportunity_id=opportunity.opportunity_id,
            revision=opportunity.revision,
            title=opportunity.opportunity.title,
            opportunity_type=opportunity.opportunity.opportunity_type,
            organization=opportunity.opportunity.organization,
            required_skills=tuple(rows),
        ),
        counts=NativeMatchCounts(
            total_opportunity_skills=len(rows),
            matched_skills=matched,
            not_recorded_skills=len(rows) - matched,
        ),
    )
