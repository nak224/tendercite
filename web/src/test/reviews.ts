import type { TenderFinding } from '../api/tendercite';
import type { ReviewEvent, ReviewRequest, ReviewedValue } from '../api/tenderciteReviews';

export const original: ReviewedValue = {
  statement: 'Synthetic PostgreSQL support is required.',
  category: 'MUST',
  requirement_type: 'TECHNICAL',
};
export const reviewFinding: TenderFinding = {
  id: 'finding-1',
  analysis_run_id: 'analysis-1',
  ...original,
  effective_value: { ...original },
  review_status: 'UNREVIEWED',
  grounding_status: 'VERIFIED_QUOTE',
  evidence: [
    {
      document_id: 'doc-1',
      page_number: 2,
      quote: original.statement,
      grounding_status: 'VERIFIED_QUOTE',
    },
  ],
};
export function reviewEvent(
  request: ReviewRequest,
  finding = reviewFinding,
  id = 'event-1',
): ReviewEvent {
  return {
    id,
    finding_id: finding.id,
    action: request.action,
    previous_status: finding.review_status,
    previous_value: finding.effective_value as ReviewedValue,
    reviewed_value:
      request.action === 'MODIFY'
        ? request.reviewed_value
        : (finding.effective_value as ReviewedValue),
    comment: request.comment,
    created_at: '2026-10-21T10:30:00Z',
  };
}
