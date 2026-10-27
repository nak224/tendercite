import { tenderGet, tenderReportPost, type TenderFinding } from './tendercite';
import { isTenderFinding } from './tenderciteReviews';
import { copyMappingOverview } from './capabilityMappings';
import {
  object,
  localId,
  hash,
  uuid,
  count,
  sameSkills,
  overviewIsValid,
} from './capabilityMappingValidation';
import {
  coverageValues,
  type AssessmentSelection,
  type AssessmentReport,
} from './capabilityReportTypes';

export class ReportInputError extends Error {}
export class ReportRequestError extends Error {}
const safeCodes = new Set([
  'CAPABILITY_REPORT_API_DISABLED',
  'INVALID_REPORT_INPUT',
  'INVALID_MAPPING_INPUT',
  'INVALID_MAPPING_SCOPE',
  'SNAPSHOT_NOT_FOUND',
  'MAPPING_STORAGE_INTEGRITY',
  'STORAGE_INTEGRITY',
  'DATABASE_FAILED',
  'CAPABILITY_REPORT_FAILED',
  'REPORT_REQUEST_TOO_LARGE',
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
    /* Never expose raw proxy/backend content or untrusted field names. */
  }
  return new ReportRequestError(`Capability report failed (HTTP ${response.status})${code}.`);
}
export type ReportFindingOption = Pick<
  TenderFinding,
  'id' | 'analysis_run_id' | 'effective_value' | 'review_status'
>;
export async function reportFindings(
  analysisId: string,
  signal: AbortSignal,
): Promise<ReportFindingOption[]> {
  if (!localId(analysisId)) throw new ReportInputError('Select an existing analysis.');
  const data = await tenderGet<unknown>(
    `findings?analysis_run_id=${encodeURIComponent(analysisId)}`,
    signal,
  );
  if (
    !Array.isArray(data) ||
    !data.every((f) => isTenderFinding(f) && localId(f.id) && f.analysis_run_id === analysisId) ||
    new Set(data.map((f) => f.id)).size !== data.length
  )
    throw new ReportRequestError('Invalid or mismatched finding selection data.');
  return data
    .map((f) => ({
      id: f.id,
      analysis_run_id: f.analysis_run_id,
      effective_value: { ...f.effective_value },
      review_status: f.review_status,
    }))
    .sort((a, b) => a.id.localeCompare(b.id));
}
export function assessmentSelection(
  analysisId: string,
  snapshotHash: string,
  findingIds: string[],
): AssessmentSelection {
  if (
    !localId(analysisId) ||
    !hash(snapshotHash) ||
    !Array.isArray(findingIds) ||
    findingIds.length > 100 ||
    !findingIds.every(localId) ||
    new Set(findingIds).size !== findingIds.length
  )
    throw new ReportInputError(
      'Select an analysis, an exact snapshot hash and at most 100 unique findings.',
    );
  return {
    schema_version: 'capability-workflow-assessment-selection-1',
    analysis_id: analysisId,
    snapshot_hash: snapshotHash,
    finding_ids: [...findingIds].sort(),
  };
}
const strings = (v: unknown): v is string[] =>
  Array.isArray(v) && v.every((s) => typeof s === 'string');
const coverage = (v: unknown) =>
  typeof v === 'string' && (coverageValues as readonly string[]).includes(v);
function countsValid(v: unknown) {
  return (
    object(v) &&
    count(v.all_required_recorded) &&
    count(v.some_required_recorded) &&
    count(v.no_required_recorded)
  );
}
function validatedOverview(v: unknown) {
  if (!object(v) || !Array.isArray(v.findings)) return null;
  const view = {
    ...v,
    schema_version: 'capability-workflow-overview-1',
    skill_relevance: 'REQUIRES_EXPLICIT_OPERATOR_SELECTION',
    findings: v.findings.map((r) => (object(r) ? r.finding : null)),
    analysis_finding_ids: v.findings.map((r) =>
      object(r) && object(r.finding) ? r.finding.finding_id : null,
    ),
  };
  return overviewIsValid(view) ? copyMappingOverview(view) : null;
}
export async function capabilityReport(
  selection: AssessmentSelection,
  signal: AbortSignal,
): Promise<AssessmentReport> {
  const request = assessmentSelection(
    selection.analysis_id,
    selection.snapshot_hash,
    selection.finding_ids,
  );
  const body = JSON.stringify(request);
  if (new TextEncoder().encode(body).byteLength > 65536)
    throw new ReportInputError('Report request exceeds the 65,536-byte limit.');
  let data: unknown;
  try {
    data = await tenderReportPost(body, signal, responseError);
  } catch (error) {
    if (signal.aborted || error instanceof ReportRequestError) throw error;
    throw new ReportRequestError(
      'Capability report unavailable or invalid. No automatic retry was made.',
    );
  }
  const overview = validatedOverview(data);
  const invalid = () =>
    new ReportRequestError('Invalid or mismatched capability report. No result displayed.');
  if (
    !object(data) ||
    !overview ||
    data.schema_version !== 'capability-workflow-report-1' ||
    data.matching_contract_version !== 'capability-matching-result-1' ||
    data.algorithm_version !== 'explicit-uuid-membership-1' ||
    data.purpose !== 'ASSESSMENT_SUPPORT_ONLY' ||
    data.qualification !== 'NOT_VERIFIED' ||
    data.capacity_evaluation !== 'NOT_EVALUATED' ||
    data.analysis_id !== request.analysis_id ||
    data.snapshot_hash !== request.snapshot_hash ||
    !Array.isArray(data.findings) ||
    !sameSkills(overview.analysis_finding_ids.slice().sort(), request.finding_ids) ||
    !strings(data.evidence_boundaries) ||
    !object(data.summary)
  )
    throw invalid();
  const summaryKeys = [
    'selected_finding_count',
    'current_approved',
    'drafts',
    'stale',
    'revoked',
    'without_mapping',
    'not_eligible_for_matching',
  ] as const;
  const summary = data.summary;
  if (
    !summaryKeys.every(
      (key) => count(summary[key]) && (summary[key] as number) <= request.finding_ids.length,
    ) ||
    summary.selected_finding_count !== request.finding_ids.length
  )
    throw invalid();
  const catalog = new Set(overview.snapshot.skill_catalog.map((s) => s.skill_id));
  const skills = (v: unknown): v is string[] =>
    strings(v) && v.every((id) => uuid(id) && catalog.has(id)) && new Set(v).size === v.length;
  const rows = data.findings;
  if (
    !rows.every(
      (r) =>
        object(r) &&
        coverage(r.coverage) &&
        r.qualification === 'NOT_VERIFIED' &&
        r.capacity_evaluation === 'NOT_EVALUATED' &&
        r.reviewed_meaning_verification === 'NOT_ASSESSED' &&
        countsValid(r.individual_counts) &&
        Array.isArray(r.individual_coverage) &&
        r.individual_coverage.length <= overview.snapshot.workforce_member_count &&
        r.individual_coverage.every(
          (c) =>
            object(c) &&
            uuid(c.person_id) &&
            coverage(c.coverage) &&
            skills(c.matched_skill_ids) &&
            skills(c.missing_assignment_ids),
        ) &&
        new Set(r.individual_coverage.map((c) => c.person_id)).size ===
          r.individual_coverage.length &&
        strings(r.non_evaluation_reasons) &&
        Array.isArray(r.limitations) &&
        r.limitations.every(
          (l) =>
            object(l) &&
            l.schema_version === 'capability-evidence-limitation-1' &&
            typeof l.code === 'string' &&
            typeof l.explanation === 'string',
        ),
    )
  )
    throw invalid();
  // Only validated contract fields are retained in component memory; discard unexpected records.
  const valid = data as unknown as AssessmentReport;
  if (
    valid.summary.current_approved +
      valid.summary.drafts +
      valid.summary.stale +
      valid.summary.revoked +
      valid.summary.without_mapping !==
      valid.findings.length ||
    valid.findings.some(
      (r) =>
        r.individual_counts.all_required_recorded +
          r.individual_counts.some_required_recorded +
          r.individual_counts.no_required_recorded !==
          r.individual_coverage.length ||
        (r.coverage === 'NOT_EVALUATED'
          ? r.individual_coverage.length !== 0
          : r.finding.mapping.status !== 'APPROVED'),
    )
  )
    throw invalid();
  return {
    schema_version: valid.schema_version,
    analysis_id: request.analysis_id,
    snapshot_hash: request.snapshot_hash,
    matching_contract_version: valid.matching_contract_version,
    algorithm_version: valid.algorithm_version,
    purpose: valid.purpose,
    snapshot: overview.snapshot,
    summary: Object.fromEntries(
      summaryKeys.map((k) => [k, summary[k]]),
    ) as unknown as AssessmentReport['summary'],
    qualification: 'NOT_VERIFIED',
    capacity_evaluation: 'NOT_EVALUATED',
    evidence_boundaries: [...valid.evidence_boundaries],
    findings: valid.findings.map((r, i) => ({
      finding: overview.findings[i]!,
      coverage: r.coverage,
      individual_counts: {
        all_required_recorded: r.individual_counts.all_required_recorded,
        some_required_recorded: r.individual_counts.some_required_recorded,
        no_required_recorded: r.individual_counts.no_required_recorded,
      },
      individual_coverage: r.individual_coverage.map((c) => ({
        person_id: c.person_id,
        matched_skill_ids: [...c.matched_skill_ids],
        missing_assignment_ids: [...c.missing_assignment_ids],
        coverage: c.coverage,
      })),
      non_evaluation_reasons: [...r.non_evaluation_reasons],
      limitations: r.limitations.map((l) => ({
        schema_version: l.schema_version,
        code: l.code,
        explanation: l.explanation,
      })),
      qualification: 'NOT_VERIFIED',
      capacity_evaluation: 'NOT_EVALUATED',
      reviewed_meaning_verification: 'NOT_ASSESSED',
    })),
  };
}
