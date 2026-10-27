import { tenderGet, tenderMappingPost } from './tendercite';
import { isTenderFinding } from './tenderciteReviews';
import { validSnapshotPeriod } from '../utils/period';
import {
  object,
  localId,
  hash,
  timestamp,
  nonblank,
  overviewIsValid,
  recordIsValid,
  sameBinding,
  sameSkills,
} from './capabilityMappingValidation';
import type {
  MappingApproval,
  MappingFinding,
  MappingOverview,
  MappingProposal,
  MappingRecord,
  MappingScope,
  ReviewBinding,
  SnapshotMetadata,
} from './capabilityMappingTypes';

export class MappingInputError extends Error {}
export class MappingRequestError extends Error {}
const safeCodes = new Set([
  'CAPABILITY_MAPPING_API_DISABLED',
  'INVALID_MAPPING_INPUT',
  'MAPPING_NOT_FOUND',
  'MAPPING_ALREADY_EXISTS',
  'MAPPING_REVISION_CONFLICT',
  'REQUIREMENT_REVIEW_CONFLICT',
  'REQUIREMENT_NOT_ELIGIBLE',
  'UNKNOWN_CATALOG_SKILL_UUID',
  'INVALID_MAPPING_TRANSITION',
  'INVALID_MAPPING_SCOPE',
  'SNAPSHOT_NOT_FOUND',
  'MAPPING_STORAGE_INTEGRITY',
  'STORAGE_INTEGRITY',
  'DATABASE_FAILED',
  'CAPABILITY_MAPPING_FAILED',
  'MAPPING_REQUEST_TOO_LARGE',
  'INVALID_CONTENT_LENGTH',
  'INCOMPLETE_BODY',
  'UNSUPPORTED_MEDIA_TYPE',
  'UNSUPPORTED_CONTENT_ENCODING',
]);
async function responseError(response: Response): Promise<Error> {
  let code = '';
  try {
    const data = await response.json();
    if (typeof data?.detail?.code === 'string' && safeCodes.has(data.detail.code))
      code = `: ${data.detail.code}`;
  } catch {
    /* Raw backend/proxy bodies, field names and exception text are never displayed. */
  }
  return new MappingRequestError(`Mapping request failed (HTTP ${response.status})${code}.`);
}
async function get(path: string, signal: AbortSignal): Promise<unknown> {
  try {
    return await tenderGet<unknown>(path, signal, responseError);
  } catch (error) {
    if (error instanceof MappingRequestError) throw error;
    throw new MappingRequestError('Mapping data unavailable or invalid. Reload explicitly.');
  }
}
export async function mappingAnalyses(signal: AbortSignal): Promise<string[]> {
  const data = await get('findings', signal);
  if (
    !Array.isArray(data) ||
    !data.every((f) => isTenderFinding(f) && localId(f.analysis_run_id)) ||
    new Set(data.map((f) => f.id)).size !== data.length
  )
    throw new MappingRequestError('Invalid analysis inventory from current findings.');
  // There is no analysis-list HTTP endpoint. Never invent one or cache all source text here.
  return [...new Set<string>(data.map((f) => f.analysis_run_id))].sort();
}
export async function mappingSnapshots(signal: AbortSignal): Promise<SnapshotMetadata[]> {
  const data = await get('capability-snapshots?limit=50', signal);
  if (
    !object(data) ||
    data.status !== 'LISTED' ||
    !Array.isArray(data.snapshots) ||
    data.snapshots.length > 50 ||
    !data.snapshots.every(
      (s) =>
        object(s) &&
        hash(s.content_hash) &&
        s.schema_version === '1.0' &&
        s.source_system === 'team-capacity-engine' &&
        typeof s.assessment_period_start === 'string' &&
        typeof s.assessment_period_end === 'string' &&
        validSnapshotPeriod({
          start_date: s.assessment_period_start,
          end_date: s.assessment_period_end,
        }) &&
        timestamp(s.imported_at),
    ) ||
    new Set(data.snapshots.map((s) => s.content_hash)).size !== data.snapshots.length
  )
    throw new MappingRequestError('Invalid imported snapshot metadata. Reload explicitly.');
  return data.snapshots.map((s) => ({
    content_hash: s.content_hash,
    schema_version: s.schema_version,
    source_system: s.source_system,
    assessment_period_start: s.assessment_period_start,
    assessment_period_end: s.assessment_period_end,
    imported_at: s.imported_at,
  }));
}
function scopeValid(scope: MappingScope) {
  return localId(scope.analysis_id) && hash(scope.snapshot_hash);
}
function copyBinding(binding: ReviewBinding): ReviewBinding {
  return {
    review_event_id: binding.review_event_id,
    digest: binding.digest,
    value: {
      statement: binding.value.statement,
      category: binding.value.category,
      requirement_type: binding.value.requirement_type,
    },
  };
}
export async function mappingOverview(
  scope: MappingScope,
  signal: AbortSignal,
): Promise<MappingOverview> {
  if (!scopeValid(scope))
    throw new MappingInputError('Select one existing analysis and an exact imported snapshot.');
  const query = new URLSearchParams({
    analysis_id: scope.analysis_id,
    snapshot_hash: scope.snapshot_hash,
  });
  const data = await get(`capability-workflow/overview?${query}`, signal);
  if (
    !overviewIsValid(data) ||
    data.analysis_id !== scope.analysis_id ||
    data.snapshot_hash !== scope.snapshot_hash
  )
    throw new MappingRequestError('Invalid or mismatched mapping overview. Reload current state.');
  return copyMappingOverview(data);
}
// Project validated source/mapping fields for both the overview and private report client.
export function copyMappingOverview(data: MappingOverview): MappingOverview {
  return {
    schema_version: data.schema_version,
    analysis_id: data.analysis_id,
    snapshot_hash: data.snapshot_hash,
    analysis_finding_ids: [...data.analysis_finding_ids],
    skill_relevance: data.skill_relevance,
    snapshot: {
      content_hash: data.snapshot.content_hash,
      schema_version: '1.0',
      source_system: 'team-capacity-engine',
      assessment_period: { ...data.snapshot.assessment_period },
      workforce_member_count: data.snapshot.workforce_member_count,
      source_limitations: [...data.snapshot.source_limitations],
      skill_catalog: data.snapshot.skill_catalog.map((s) => ({
        skill_id: s.skill_id,
        name: s.name,
        category: s.category,
      })),
    },
    findings: data.findings.map((f) => ({
      finding_id: f.finding_id,
      original_requirement: { ...f.original_requirement },
      effective_requirement: { ...f.effective_requirement },
      review_status: f.review_status,
      grounding_status: f.grounding_status,
      mapping_preparation_eligible: f.mapping_preparation_eligible,
      current_review_binding: copyBinding(f.current_review_binding),
      source_references: f.source_references.map((e) => ({
        document_id: e.document_id,
        page_number: e.page_number,
        quote: e.quote,
        grounding_status: e.grounding_status,
      })),
      mapping: {
        status: f.mapping.status,
        mapping_id: f.mapping.mapping_id,
        revision: f.mapping.revision,
        operator: f.mapping.operator,
        skill_ids: [...f.mapping.skill_ids],
        unavailable_reasons: [...f.mapping.unavailable_reasons],
        approved_review_binding: f.mapping.approved_review_binding
          ? copyBinding(f.mapping.approved_review_binding)
          : null,
      },
    })),
  };
}
function declaration(rationale: string, alias: string) {
  if (!nonblank(rationale, 2000) || (alias !== '' && !/^[a-zA-Z0-9_-]{1,64}$/.test(alias)))
    throw new MappingInputError(
      'Enter a nonblank rationale (maximum 2000 characters) and an optional non-personal alias (letters, digits, _ or -, maximum 64).',
    );
  return { rationale, operator_alias: alias || null };
}
function currentFinding(overview: MappingOverview, finding: MappingFinding) {
  if (
    !scopeValid(overview) ||
    !overview.findings.includes(finding) ||
    !finding.mapping_preparation_eligible
  )
    throw new MappingInputError(
      'Select a current reviewed finding with verified source grounding.',
    );
}
export function proposeMapping(
  overview: MappingOverview,
  finding: MappingFinding,
  skills: string[],
  rationale: string,
  alias: string,
): MappingProposal {
  currentFinding(overview, finding);
  if (
    finding.mapping.status !== 'NOT_FOUND' ||
    !skills.length ||
    skills.length > 100 ||
    new Set(skills).size !== skills.length ||
    !skills.every((id) => overview.snapshot.skill_catalog.some((s) => s.skill_id === id))
  )
    throw new MappingInputError(
      'Select 1–100 unique skills from this snapshot for a finding without a mapping.',
    );
  return {
    selection: {
      schema_version: 'capability-workflow-mapping-selection-1',
      analysis_id: overview.analysis_id,
      snapshot_hash: overview.snapshot_hash,
      finding_id: finding.finding_id,
      operator: 'ALL_OF',
      skill_ids: [...skills].sort(),
      ...declaration(rationale, alias),
    },
    binding: copyBinding(finding.current_review_binding),
  };
}
export function approvalInput(
  overview: MappingOverview,
  finding: MappingFinding,
  rationale: string,
  alias: string,
): MappingApproval {
  currentFinding(overview, finding);
  const m = finding.mapping;
  const b = finding.current_review_binding;
  if (m.status !== 'DRAFT' || !m.mapping_id || !m.revision || !b.review_event_id)
    throw new MappingInputError(
      'Only a current DRAFT can be explicitly approved. Reload before continuing.',
    );
  return {
    schema_version: 'capability-mapping-approval-1',
    mapping_id: m.mapping_id,
    expected_revision: m.revision,
    expected_review_event_id: b.review_event_id,
    expected_review_digest: b.digest,
    ...declaration(rationale, alias),
  };
}
async function post(
  operation: 'drafts' | 'approve',
  input: MappingProposal['selection'] | MappingApproval,
): Promise<MappingRecord> {
  const body = JSON.stringify(input);
  if (new TextEncoder().encode(body).byteLength > 65536)
    throw new MappingInputError('Mapping request exceeds the 64 KiB limit.');
  let data: unknown;
  try {
    data = await tenderMappingPost(
      operation,
      body,
      responseError,
      'mapping_id' in input ? input.mapping_id : undefined,
    );
  } catch (error) {
    if (error instanceof MappingRequestError) throw error;
    throw new MappingRequestError('Mapping response unavailable or invalid.');
  }
  if (!recordIsValid(data)) throw new MappingRequestError('Invalid mapping confirmation.');
  return data;
}
export async function createMappingDraft(proposal: MappingProposal): Promise<MappingRecord> {
  const s = proposal.selection;
  const record = await post('drafts', s);
  if (
    record.status !== 'DRAFT' ||
    record.revision !== 1 ||
    record.analysis_id !== s.analysis_id ||
    record.finding_id !== s.finding_id ||
    record.snapshot_hash !== s.snapshot_hash ||
    !sameSkills(record.skill_ids, s.skill_ids) ||
    !sameBinding(record.review_binding, proposal.binding) ||
    record.rationale !== s.rationale ||
    record.operator_alias !== s.operator_alias
  )
    throw new MappingRequestError(
      'Draft confirmation differs from the proposed selection or review. A draft may be stored; inspect refreshed state.',
    );
  return record;
}
export async function approveMapping(
  overview: MappingOverview,
  finding: MappingFinding,
  rationale: string,
  alias: string,
): Promise<MappingRecord> {
  const input = approvalInput(overview, finding, rationale, alias);
  const record = await post('approve', input);
  if (
    record.status !== 'APPROVED' ||
    record.mapping_id !== input.mapping_id ||
    record.revision !== input.expected_revision + 1 ||
    record.analysis_id !== overview.analysis_id ||
    record.finding_id !== finding.finding_id ||
    record.snapshot_hash !== overview.snapshot_hash ||
    !sameSkills(record.skill_ids, finding.mapping.skill_ids) ||
    !sameBinding(record.review_binding, finding.current_review_binding) ||
    record.rationale !== input.rationale ||
    record.operator_alias !== input.operator_alias
  )
    throw new MappingRequestError(
      'Approval confirmation differs from the selected draft or current review.',
    );
  return record;
}
