import type { TenderFinding } from '../api/tendercite';
import type { AssessmentRow } from '../api/tenderciteAssessments';
import { reviewFinding } from './reviews';

export const assessmentFindings: TenderFinding[] = [
  { ...structuredClone(reviewFinding), review_status: 'CONFIRMED' },
  { ...structuredClone(reviewFinding), id: 'finding-2', review_status: 'UNREVIEWED' },
  { ...structuredClone(reviewFinding), id: 'finding-3', review_status: 'REJECTED' },
];
export function matrixRow(
  finding = assessmentFindings[0]!,
  values: Partial<AssessmentRow> = {},
): AssessmentRow {
  return {
    finding_id: finding.id,
    criterion: finding.effective_value.statement,
    category: finding.effective_value.category,
    requirement_type: finding.effective_value.requirement_type,
    review_status: finding.review_status as AssessmentRow['review_status'],
    grounding_status: finding.grounding_status,
    evidence: finding.evidence,
    status: 'CLARIFICATION_NEEDED',
    rationale: null,
    ...values,
  };
}
