import { categories, requirementTypes } from './tenderciteIntake';
import type { ReviewedValue } from './tenderciteReviews';
import type {
  MappingFinding,
  MappingOverview,
  MappingRecord,
  ReviewBinding,
} from './capabilityMappingTypes';
import { validSnapshotPeriod } from '../utils/period';

export const object = (v: unknown): v is Record<string, unknown> =>
  !!v && typeof v === 'object' && !Array.isArray(v);
export const localId = (v: unknown): v is string =>
  typeof v === 'string' && [...v].length <= 128 && /^\S+$/u.test(v);
export const hash = (v: unknown): v is string =>
  typeof v === 'string' && /^sha256:[a-f0-9]{64}$/.test(v);
export const uuid = (v: unknown): v is string =>
  typeof v === 'string' && /^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/.test(v);
export const count = (v: unknown): v is number =>
  typeof v === 'number' && Number.isSafeInteger(v) && v >= 0;
export const timestamp = (v: unknown): v is string =>
  typeof v === 'string' && /^\d{4}-\d{2}-\d{2}T/.test(v) && Number.isFinite(Date.parse(v));
export const nonblank = (v: unknown, max: number): v is string =>
  typeof v === 'string' && /\S/u.test(v) && [...v].length <= max;
const member = (v: unknown, values: readonly string[]): boolean =>
  typeof v === 'string' && values.includes(v);
const unique = (ids: string[]) => new Set(ids).size === ids.length;
export const sameSkills = (a: string[], b: string[]) =>
  a.length === b.length && a.every((id, index) => id === b[index]);
const skills = (v: unknown): v is string[] =>
  Array.isArray(v) && v.length <= 100 && v.every(uuid) && unique(v) && sameSkills(v, [...v].sort());
function value(v: unknown): v is ReviewedValue {
  return (
    object(v) &&
    nonblank(v.statement, 4000) &&
    member(v.category, categories) &&
    member(v.requirement_type, requirementTypes)
  );
}
export const sameValue = (a: ReviewedValue, b: ReviewedValue) =>
  a.statement === b.statement &&
  a.category === b.category &&
  a.requirement_type === b.requirement_type;
export const sameBinding = (a: ReviewBinding, b: ReviewBinding) =>
  a.review_event_id === b.review_event_id && a.digest === b.digest && sameValue(a.value, b.value);
function binding(v: unknown): v is ReviewBinding {
  return (
    object(v) &&
    (v.review_event_id === null || localId(v.review_event_id)) &&
    hash(v.digest) &&
    value(v.value)
  );
}
const reasons = [
  'NO_PERSISTED_MAPPING',
  'MAPPING_NOT_APPROVED',
  'MAPPING_REVOKED',
  'REQUIREMENT_REVIEW_EVENT_CHANGED',
  'REQUIREMENT_INTERPRETATION_CHANGED',
  'REQUIREMENT_NOT_REVIEWED_OR_REJECTED',
  'SOURCE_QUOTE_NOT_VERIFIED',
];
function finding(v: unknown, catalog: Set<string>): v is MappingFinding {
  if (
    !object(v) ||
    !localId(v.finding_id) ||
    !value(v.original_requirement) ||
    !value(v.effective_requirement) ||
    !binding(v.current_review_binding) ||
    !sameValue(v.effective_requirement, v.current_review_binding.value) ||
    !member(v.review_status, ['UNREVIEWED', 'CONFIRMED', 'MODIFIED', 'REJECTED']) ||
    !member(v.grounding_status, ['VERIFIED_QUOTE', 'INVALID_QUOTE', 'MISSING_EVIDENCE'])
  )
    return false;
  const eligible =
    v.current_review_binding.review_event_id !== null &&
    ['CONFIRMED', 'MODIFIED'].includes(String(v.review_status)) &&
    v.grounding_status === 'VERIFIED_QUOTE';
  if (
    v.mapping_preparation_eligible !== eligible ||
    !Array.isArray(v.source_references) ||
    !v.source_references.every(
      (e) =>
        object(e) &&
        localId(e.document_id) &&
        count(e.page_number) &&
        e.page_number >= 1 &&
        typeof e.quote === 'string' &&
        member(e.grounding_status, ['VERIFIED_QUOTE', 'INVALID_QUOTE', 'MISSING_EVIDENCE']),
    )
  )
    return false;
  const m = v.mapping;
  if (
    !object(m) ||
    !member(m.status, ['NOT_FOUND', 'DRAFT', 'APPROVED', 'STALE', 'REVOKED']) ||
    !skills(m.skill_ids) ||
    !m.skill_ids.every((id) => catalog.has(id)) ||
    !Array.isArray(m.unavailable_reasons) ||
    !m.unavailable_reasons.every((r) => member(r, reasons))
  )
    return false;
  if (m.status === 'NOT_FOUND')
    return (
      m.mapping_id === null &&
      m.revision === null &&
      m.operator === null &&
      m.skill_ids.length === 0 &&
      m.approved_review_binding === null
    );
  if (
    !uuid(m.mapping_id) ||
    !count(m.revision) ||
    m.revision < 1 ||
    m.operator !== 'ALL_OF' ||
    !m.skill_ids.length
  )
    return false;
  if (m.status === 'APPROVED' || m.status === 'STALE')
    return (
      binding(m.approved_review_binding) &&
      (m.status !== 'APPROVED' ||
        (eligible && sameBinding(m.approved_review_binding, v.current_review_binding)))
    );
  return m.approved_review_binding === null;
}
export function overviewIsValid(v: unknown): v is MappingOverview {
  if (
    !object(v) ||
    v.schema_version !== 'capability-workflow-overview-1' ||
    !localId(v.analysis_id) ||
    !hash(v.snapshot_hash) ||
    v.skill_relevance !== 'REQUIRES_EXPLICIT_OPERATOR_SELECTION' ||
    !Array.isArray(v.analysis_finding_ids) ||
    !v.analysis_finding_ids.every(localId) ||
    !unique(v.analysis_finding_ids) ||
    !Array.isArray(v.findings) ||
    !object(v.snapshot)
  )
    return false;
  const s = v.snapshot;
  if (
    s.content_hash !== v.snapshot_hash ||
    s.schema_version !== '1.0' ||
    s.source_system !== 'team-capacity-engine' ||
    !object(s.assessment_period) ||
    typeof s.assessment_period.start_date !== 'string' ||
    typeof s.assessment_period.end_date !== 'string' ||
    !validSnapshotPeriod({
      start_date: s.assessment_period.start_date,
      end_date: s.assessment_period.end_date,
    }) ||
    !count(s.workforce_member_count) ||
    !Array.isArray(s.skill_catalog) ||
    !s.skill_catalog.every(
      (c) => object(c) && uuid(c.skill_id) && nonblank(c.name, 100) && nonblank(c.category, 100),
    ) ||
    !unique(s.skill_catalog.map((c) => c.skill_id)) ||
    !Array.isArray(s.source_limitations) ||
    !s.source_limitations.every((r) => typeof r === 'string')
  )
    return false;
  const catalog = new Set<string>(s.skill_catalog.map((c) => c.skill_id));
  return (
    v.findings.every((f) => finding(f, catalog)) &&
    unique(v.findings.map((f) => f.finding_id)) &&
    sameSkills([...v.analysis_finding_ids].sort(), v.findings.map((f) => f.finding_id).sort())
  );
}
export function recordIsValid(v: unknown): v is MappingRecord {
  return (
    object(v) &&
    v.schema_version === 'capability-mapping-record-1' &&
    v.matching_contract_version === 'requirement-skill-mapping-1' &&
    uuid(v.mapping_id) &&
    localId(v.analysis_id) &&
    localId(v.finding_id) &&
    hash(v.snapshot_hash) &&
    v.operator === 'ALL_OF' &&
    skills(v.skill_ids) &&
    v.skill_ids.length > 0 &&
    count(v.revision) &&
    v.revision > 0 &&
    member(v.status, ['DRAFT', 'APPROVED', 'REVOKED']) &&
    binding(v.review_binding) &&
    nonblank(v.rationale, 2000) &&
    (v.operator_alias === null ||
      (typeof v.operator_alias === 'string' && /^[a-zA-Z0-9_-]{1,64}$/.test(v.operator_alias))) &&
    timestamp(v.created_at) &&
    timestamp(v.recorded_at) &&
    Date.parse(v.created_at) <= Date.parse(v.recorded_at) &&
    (v.status === 'APPROVED'
      ? timestamp(v.approved_at) &&
        v.approved_at === v.recorded_at &&
        v.review_binding.review_event_id !== null
      : v.approved_at === null)
  );
}
