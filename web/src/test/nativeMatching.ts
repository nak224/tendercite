// Entirely synthetic comparison over saved native declarations.
import { profileRecord, skillId } from './profiles';
import { opportunityRecord } from './opportunities';
import type { ProfileMetadata } from '../api/profiles';
import type { OpportunityMetadata } from '../api/opportunities';
import type { NativeMatchResult } from '../api/nativeMatching';

export function profileMetadata(): ProfileMetadata {
  const r = profileRecord();
  return {
    profile_id: r.profile_id,
    revision: r.revision,
    updated_at: r.updated_at,
    name: r.profile.name,
    profile_type: r.profile.profile_type,
  };
}
export function opportunityMetadata(): OpportunityMetadata {
  const r = opportunityRecord();
  return {
    opportunity_id: r.opportunity_id,
    revision: r.revision,
    updated_at: r.updated_at,
    title: r.opportunity.title,
    opportunity_type: r.opportunity.opportunity_type,
    organization: r.opportunity.organization,
    deadline: r.opportunity.deadline,
  };
}
export function matchResult(): NativeMatchResult {
  const p = profileMetadata(),
    o = opportunityMetadata();
  return {
    schema_version: 'native-match-result-1',
    algorithm_version: 'nfkc-casefold-whitespace-equality-1',
    status: 'COMPARED',
    profile: {
      profile_id: p.profile_id,
      revision: p.revision,
      name: p.name,
      profile_type: p.profile_type,
      skills: [{ skill_id: skillId, original_label: 'Python', normalized_label: 'python' }],
    },
    opportunity: {
      opportunity_id: o.opportunity_id,
      revision: o.revision,
      title: o.title,
      opportunity_type: o.opportunity_type,
      organization: o.organization,
      required_skills: [
        {
          original_label: 'python',
          normalized_label: 'python',
          status: 'MATCHED',
          matched_profile_skill_ids: [skillId],
        },
        {
          original_label: 'FastAPI',
          normalized_label: 'fastapi',
          status: 'NOT_RECORDED',
          matched_profile_skill_ids: [],
        },
      ],
    },
    counts: { total_opportunity_skills: 2, matched_skills: 1, not_recorded_skills: 1 },
    evidence_limitations: ['Manually entered labels; no independently verified qualification.'],
    qualification: 'NOT_VERIFIED',
    availability: 'NOT_EVALUATED',
    eligibility: 'NOT_VERIFIED',
  };
}
