import { tenderGet, tenderAssessmentPatch, type TenderFinding } from './tendercite';
import { isTenderFinding } from './tenderciteReviews';
import { categories, requirementTypes } from './tenderciteIntake';

export const assessmentStatuses = [
  'FULFILLED',
  'PARTIAL',
  'MISSING',
  'CLARIFICATION_NEEDED',
  'NOT_APPLICABLE',
] as const;
export interface AssessmentRequest {
  status: (typeof assessmentStatuses)[number];
  rationale: string;
}
export interface AssessmentRow {
  finding_id: string;
  criterion: string;
  category: string;
  requirement_type: string;
  review_status: 'CONFIRMED' | 'MODIFIED';
  grounding_status: string;
  evidence: TenderFinding['evidence'];
  status: AssessmentRequest['status'];
  rationale: string | null;
}
export interface AssessmentContext {
  findings: TenderFinding[];
  rows: AssessmentRow[];
}
export class AssessmentInputError extends Error {}
export class AssessmentSaveError extends Error {}
export const assessmentEligible = (finding: TenderFinding) =>
  finding.review_status === 'CONFIRMED' || finding.review_status === 'MODIFIED';

function object(value: unknown): value is Record<string, unknown> {
  return !!value && typeof value === 'object' && !Array.isArray(value);
}
function member(value: unknown, options: readonly string[]): value is string {
  return typeof value === 'string' && options.includes(value);
}
function isRow(row: unknown): row is AssessmentRow {
  return (
    object(row) &&
    typeof row.finding_id === 'string' &&
    !!row.finding_id &&
    typeof row.criterion === 'string' &&
    member(row.category, categories) &&
    member(row.requirement_type, requirementTypes) &&
    member(row.review_status, ['CONFIRMED', 'MODIFIED']) &&
    member(row.grounding_status, ['VERIFIED_QUOTE', 'INVALID_QUOTE', 'MISSING_EVIDENCE']) &&
    member(row.status, assessmentStatuses) &&
    (row.rationale === null ||
      (typeof row.rationale === 'string' &&
        /\S/u.test(row.rationale) &&
        [...row.rationale].length <= 4000)) &&
    Array.isArray(row.evidence) &&
    row.evidence.every(
      (e: unknown) =>
        object(e) &&
        typeof e.document_id === 'string' &&
        !!e.document_id &&
        Number.isSafeInteger(e.page_number) &&
        (e.page_number as number) > 0 &&
        typeof e.quote === 'string' &&
        member(e.grounding_status, ['VERIFIED_QUOTE', 'INVALID_QUOTE', 'MISSING_EVIDENCE']),
    )
  );
}
export function assessmentRequest(status: string, rationale: string): AssessmentRequest {
  if (!member(status, assessmentStatuses) || !/\S/u.test(rationale) || [...rationale].length > 4000)
    throw new AssessmentInputError(
      'Select an assessment status and enter a nonblank rationale (1–4000 characters).',
    );
  return { status: status as AssessmentRequest['status'], rationale };
}
function evidenceIdentity(evidence: TenderFinding['evidence']) {
  return JSON.stringify(
    evidence.map((e) => [e.document_id, e.page_number, e.quote, e.grounding_status]),
  );
}
export async function assessmentContext(
  analysisId: string,
  signal: AbortSignal,
): Promise<AssessmentContext> {
  const filter = `?analysis_run_id=${encodeURIComponent(analysisId)}`;
  const [findings, rows] = await Promise.all([
    tenderGet<unknown>(`findings${filter}`, signal),
    tenderGet<unknown>(`go-no-go${filter}`, signal),
  ]);
  if (
    !Array.isArray(findings) ||
    !findings.every((f) => isTenderFinding(f) && f.analysis_run_id === analysisId) ||
    new Set(findings.map((f) => f.id)).size !== findings.length ||
    !Array.isArray(rows) ||
    !rows.every(isRow) ||
    new Set(rows.map((r) => r.finding_id)).size !== rows.length
  )
    throw new Error('TenderCite returned invalid assessment data for this analysis.');
  const eligible = findings.filter(assessmentEligible);
  if (
    rows.length !== eligible.length ||
    rows.some((row) => {
      const f = eligible.find((finding) => finding.id === row.finding_id);
      return (
        !f ||
        row.criterion !== f.effective_value.statement ||
        row.category !== f.effective_value.category ||
        row.requirement_type !== f.effective_value.requirement_type ||
        row.review_status !== f.review_status ||
        row.grounding_status !== f.grounding_status ||
        evidenceIdentity(row.evidence) !== evidenceIdentity(f.evidence)
      );
    })
  )
    throw new Error(
      'Finding and assessment state changed or is inconsistent. Reload current data.',
    );
  return { findings: findings.sort((a, b) => a.id.localeCompare(b.id)), rows };
}
export async function saveAssessment(
  findingId: string,
  input: AssessmentRequest,
): Promise<AssessmentRow> {
  const request = assessmentRequest(input.status, input.rationale);
  let row: unknown;
  try {
    row = await tenderAssessmentPatch(
      findingId,
      JSON.stringify(request),
      async (response) =>
        new AssessmentSaveError(
          response.status === 409
            ? 'Assessment conflict (HTTP 409). Review eligibility may have changed.'
            : `Assessment request failed (HTTP ${response.status}).`,
        ),
    );
  } catch (error) {
    if (error instanceof AssessmentSaveError) throw error;
    throw new AssessmentSaveError('Assessment response unavailable or invalid.');
  }
  if (
    !isRow(row) ||
    row.finding_id !== findingId ||
    row.status !== request.status ||
    row.rationale !== request.rationale
  )
    throw new AssessmentSaveError('TenderCite returned an invalid assessment confirmation.');
  return row;
}
