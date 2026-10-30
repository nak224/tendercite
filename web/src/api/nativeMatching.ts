import { tenderNativeMatchPost } from './tendercite';
import { object, uuid, nonblank, count } from './capabilityMappingValidation';
import type { ProfileMetadata, ProfileType } from './profiles';
import type { OpportunityMetadata, OpportunityType } from './opportunities';

export interface NativeMatchSelection {
  schema_version: 'native-match-selection-1';
  profile_id: string;
  opportunity_id: string;
  expected_profile_revision: number;
  expected_opportunity_revision: number;
}
interface ProfileLabel {
  skill_id: string;
  original_label: string;
  normalized_label: string;
}
interface RequiredLabel {
  original_label: string;
  normalized_label: string;
  status: 'MATCHED' | 'NOT_RECORDED';
  matched_profile_skill_ids: string[];
}
export interface NativeMatchResult {
  schema_version: 'native-match-result-1';
  algorithm_version: 'nfkc-casefold-whitespace-equality-1';
  status: 'COMPARED' | 'NO_REQUIREMENTS';
  profile: {
    profile_id: string;
    revision: number;
    name: string;
    profile_type: ProfileType;
    skills: ProfileLabel[];
  };
  opportunity: {
    opportunity_id: string;
    revision: number;
    title: string;
    opportunity_type: OpportunityType;
    organization: string;
    required_skills: RequiredLabel[];
  };
  counts: { total_opportunity_skills: number; matched_skills: number; not_recorded_skills: number };
  evidence_limitations: string[];
  qualification: 'NOT_VERIFIED';
  availability: 'NOT_EVALUATED';
  eligibility: 'NOT_VERIFIED';
}
export class NativeMatchError extends Error {}
const codes = new Set([
  'NATIVE_MATCHING_API_DISABLED',
  'INVALID_NATIVE_MATCH_INPUT',
  'NATIVE_MATCH_REQUEST_TOO_LARGE',
  'NATIVE_MATCH_FAILED',
  'PROFILE_NOT_FOUND',
  'OPPORTUNITY_NOT_FOUND',
  'PROFILE_REVISION_CONFLICT',
  'OPPORTUNITY_REVISION_CONFLICT',
  'PROFILE_STORAGE_INTEGRITY',
  'OPPORTUNITY_STORAGE_INTEGRITY',
  'DATABASE_FAILED',
  'INVALID_CONTENT_LENGTH',
  'INCOMPLETE_BODY',
  'UNSUPPORTED_MEDIA_TYPE',
  'UNSUPPORTED_CONTENT_ENCODING',
]);
async function responseError(response: Response): Promise<Error> {
  let code = '';
  try {
    const body = await response.json();
    if (typeof body?.detail?.code === 'string' && codes.has(body.detail.code))
      code = body.detail.code;
  } catch {
    /* Never show raw backend/proxy responses. */
  }
  return new NativeMatchError(
    `Comparison failed (HTTP ${response.status})${code ? `: ${code}` : ''}. Refresh saved records before comparing again. No automatic retry was made.`,
  );
}
const revision = (v: unknown): v is number => count(v) && v >= 1 && v <= 2147483647;
const unique = (values: string[]) => new Set(values).size === values.length;
function labels(
  v: unknown,
): v is Record<string, unknown> & { original_label: string; normalized_label: string } {
  return object(v) && nonblank(v.original_label, 100) && nonblank(v.normalized_label, 4096);
}
function valid(v: unknown, p: ProfileMetadata, o: OpportunityMetadata): v is NativeMatchResult {
  if (
    !object(v) ||
    v.schema_version !== 'native-match-result-1' ||
    v.algorithm_version !== 'nfkc-casefold-whitespace-equality-1' ||
    v.qualification !== 'NOT_VERIFIED' ||
    v.availability !== 'NOT_EVALUATED' ||
    v.eligibility !== 'NOT_VERIFIED' ||
    !object(v.profile) ||
    !object(v.opportunity) ||
    !object(v.counts)
  )
    return false;
  const profile = v.profile,
    opportunity = v.opportunity;
  if (
    profile.profile_id !== p.profile_id ||
    profile.revision !== p.revision ||
    profile.name !== p.name ||
    profile.profile_type !== p.profile_type ||
    opportunity.opportunity_id !== o.opportunity_id ||
    opportunity.revision !== o.revision ||
    opportunity.title !== o.title ||
    opportunity.opportunity_type !== o.opportunity_type ||
    opportunity.organization !== o.organization ||
    !Array.isArray(profile.skills) ||
    profile.skills.length > 100 ||
    !profile.skills.every((s) => labels(s) && object(s) && uuid(s.skill_id)) ||
    !unique(profile.skills.map((s) => s.skill_id)) ||
    !unique(profile.skills.map((s) => s.normalized_label)) ||
    !Array.isArray(opportunity.required_skills) ||
    opportunity.required_skills.length > 100 ||
    !opportunity.required_skills.every(
      (s) =>
        labels(s) &&
        object(s) &&
        ['MATCHED', 'NOT_RECORDED'].includes(s.status as string) &&
        Array.isArray(s.matched_profile_skill_ids) &&
        s.matched_profile_skill_ids.length <= 1 &&
        s.matched_profile_skill_ids.every(uuid),
    ) ||
    !unique(opportunity.required_skills.map((s) => s.normalized_label)) ||
    !Array.isArray(v.evidence_limitations) ||
    v.evidence_limitations.length < 1 ||
    v.evidence_limitations.length > 10 ||
    !v.evidence_limitations.every((s) => nonblank(s, 1000))
  )
    return false;
  // Check response consistency using server-normalized labels. Python casefold is authoritative;
  // JavaScript lowercasing is deliberately not a second implementation of the matcher.
  for (const row of opportunity.required_skills) {
    const matching = profile.skills.filter((s) => s.normalized_label === row.normalized_label);
    if (row.status === 'MATCHED') {
      if (
        matching.length !== 1 ||
        row.matched_profile_skill_ids.length !== 1 ||
        row.matched_profile_skill_ids[0] !== matching[0].skill_id
      )
        return false;
    } else if (matching.length || row.matched_profile_skill_ids.length) return false;
  }
  const total = opportunity.required_skills.length;
  const matched = opportunity.required_skills.filter((s) => s.status === 'MATCHED').length;
  return (
    v.status === (total ? 'COMPARED' : 'NO_REQUIREMENTS') &&
    v.counts.total_opportunity_skills === total &&
    v.counts.matched_skills === matched &&
    v.counts.not_recorded_skills === total - matched
  );
}
export async function compareNativeSkills(
  profile: ProfileMetadata,
  opportunity: OpportunityMetadata,
  signal: AbortSignal,
): Promise<NativeMatchResult> {
  if (
    !uuid(profile.profile_id) ||
    !uuid(opportunity.opportunity_id) ||
    !revision(profile.revision) ||
    !revision(opportunity.revision)
  )
    throw new NativeMatchError('Select saved profile and opportunity versions.');
  const selection: NativeMatchSelection = {
    schema_version: 'native-match-selection-1',
    profile_id: profile.profile_id,
    opportunity_id: opportunity.opportunity_id,
    expected_profile_revision: profile.revision,
    expected_opportunity_revision: opportunity.revision,
  };
  let result: unknown;
  try {
    result = await tenderNativeMatchPost(JSON.stringify(selection), signal, responseError);
  } catch (error) {
    if (signal.aborted || error instanceof NativeMatchError) throw error;
    throw new NativeMatchError(
      'Comparison unavailable or invalid. Refresh saved records. No automatic retry was made.',
    );
  }
  if (!valid(result, profile, opportunity))
    throw new NativeMatchError('Invalid or mismatched comparison response. Refresh saved records.');
  // Project only the documented fields; never preserve arbitrary server records.
  return {
    schema_version: result.schema_version,
    algorithm_version: result.algorithm_version,
    status: result.status,
    profile: {
      profile_id: profile.profile_id,
      revision: profile.revision,
      name: profile.name,
      profile_type: profile.profile_type,
      skills: result.profile.skills.map((s) => ({
        skill_id: s.skill_id,
        original_label: s.original_label,
        normalized_label: s.normalized_label,
      })),
    },
    opportunity: {
      opportunity_id: opportunity.opportunity_id,
      revision: opportunity.revision,
      title: opportunity.title,
      opportunity_type: opportunity.opportunity_type,
      organization: opportunity.organization,
      required_skills: result.opportunity.required_skills.map((s) => ({
        original_label: s.original_label,
        normalized_label: s.normalized_label,
        status: s.status,
        matched_profile_skill_ids: [...s.matched_profile_skill_ids],
      })),
    },
    counts: {
      total_opportunity_skills: result.counts.total_opportunity_skills,
      matched_skills: result.counts.matched_skills,
      not_recorded_skills: result.counts.not_recorded_skills,
    },
    evidence_limitations: [...result.evidence_limitations],
    qualification: result.qualification,
    availability: result.availability,
    eligibility: result.eligibility,
  };
}
