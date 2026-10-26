import { tenderGet, tenderReviewPost, type TenderFinding } from './tendercite';
import { categories, requirementTypes } from './tenderciteIntake';

export const reviewActions = ['CONFIRM', 'MODIFY', 'REJECT'] as const;
const reviewStatuses = ['UNREVIEWED', 'CONFIRMED', 'MODIFIED', 'REJECTED'];
const groundingStatuses = ['VERIFIED_QUOTE', 'MISSING_EVIDENCE', 'INVALID_QUOTE'];
export type ReviewAction = (typeof reviewActions)[number];
export interface ReviewedValue {
  statement: string;
  category: (typeof categories)[number];
  requirement_type: (typeof requirementTypes)[number];
}
export type ReviewRequest =
  | { action: 'MODIFY'; reviewed_value: ReviewedValue; comment: string }
  | { action: 'CONFIRM' | 'REJECT'; comment: string };
export interface ReviewEvent {
  id: string;
  finding_id: string;
  action: ReviewAction;
  previous_status: string;
  previous_value: ReviewedValue;
  reviewed_value: ReviewedValue;
  comment: string;
  created_at: string;
}
export class ReviewInputError extends Error {}
export class ReviewSubmissionError extends Error {}
function object(value: unknown): value is Record<string, unknown> {
  return !!value && typeof value === 'object' && !Array.isArray(value);
}
function member(value: unknown, options: readonly string[]): value is string {
  return typeof value === 'string' && options.includes(value);
}
function valueIsValid(value: unknown): value is ReviewedValue {
  return (
    object(value) &&
    typeof value.statement === 'string' &&
    /\S/u.test(value.statement) &&
    [...value.statement].length <= 4000 &&
    member(value.category, categories) &&
    member(value.requirement_type, requirementTypes)
  );
}
export function reviewRequest(
  action: ReviewAction,
  value: ReviewedValue,
  comment: string,
): ReviewRequest {
  if (!member(action, reviewActions) || [...comment].length > 4000)
    throw new ReviewInputError('Select a review action and use at most 4000 comment characters.');
  if (action === 'MODIFY') {
    if (!valueIsValid(value))
      throw new ReviewInputError(
        'Modify requires a statement (1–4000 characters), category and type.',
      );
    return {
      action,
      reviewed_value: {
        statement: value.statement,
        category: value.category,
        requirement_type: value.requirement_type,
      },
      comment,
    };
  }
  return { action, comment };
}
function eventIsValid(value: unknown, findingId: string): value is ReviewEvent {
  return (
    object(value) &&
    typeof value.id === 'string' &&
    !!value.id &&
    value.finding_id === findingId &&
    member(value.action, reviewActions) &&
    member(value.previous_status, reviewStatuses) &&
    valueIsValid(value.previous_value) &&
    valueIsValid(value.reviewed_value) &&
    typeof value.comment === 'string' &&
    [...value.comment].length <= 4000 &&
    typeof value.created_at === 'string' &&
    Number.isFinite(Date.parse(value.created_at))
  );
}
export function isTenderFinding(finding: unknown, id?: string): finding is TenderFinding {
  return !(
    !object(finding) ||
    typeof finding.id !== 'string' ||
    !finding.id ||
    (id !== undefined && finding.id !== id) ||
    typeof finding.analysis_run_id !== 'string' ||
    typeof finding.statement !== 'string' ||
    !member(finding.category, categories) ||
    !member(finding.requirement_type, requirementTypes) ||
    !valueIsValid(finding.effective_value) ||
    !member(finding.review_status, reviewStatuses) ||
    !member(finding.grounding_status, groundingStatuses) ||
    !Array.isArray(finding.evidence) ||
    !finding.evidence.every(
      (e: unknown) =>
        object(e) &&
        typeof e.document_id === 'string' &&
        !!e.document_id &&
        Number.isSafeInteger(e.page_number) &&
        (e.page_number as number) > 0 &&
        typeof e.quote === 'string' &&
        member(e.grounding_status, groundingStatuses),
    )
  );
}
export async function findingForReview(id: string, signal: AbortSignal): Promise<TenderFinding> {
  const finding = await tenderGet<unknown>(`findings/${encodeURIComponent(id)}`, signal);
  if (!isTenderFinding(finding, id))
    throw new Error('TenderCite returned an invalid finding response.');
  return finding;
}
export async function reviewHistory(id: string, signal: AbortSignal): Promise<ReviewEvent[]> {
  const events = await tenderGet<unknown>(`findings/${encodeURIComponent(id)}/reviews`, signal);
  if (
    !Array.isArray(events) ||
    !events.every((event) => eventIsValid(event, id)) ||
    new Set(events.map((event) => event.id)).size !== events.length
  )
    throw new Error('TenderCite returned an invalid review history.');
  return events;
}
export async function saveReview(id: string, request: ReviewRequest): Promise<ReviewEvent> {
  const payload = reviewRequest(
    request.action,
    request.action === 'MODIFY' ? request.reviewed_value : ({} as ReviewedValue),
    request.comment,
  );
  let event: unknown;
  try {
    event = await tenderReviewPost(
      id,
      JSON.stringify(payload),
      async (response) =>
        new ReviewSubmissionError(`Review request failed (HTTP ${response.status}).`),
    );
  } catch (error) {
    if (error instanceof ReviewSubmissionError) throw error;
    throw new ReviewSubmissionError('Review response unavailable or invalid.');
  }
  if (
    !eventIsValid(event, id) ||
    event.action !== request.action ||
    event.comment !== request.comment ||
    (request.action === 'MODIFY' &&
      (event.reviewed_value.statement !== request.reviewed_value.statement ||
        event.reviewed_value.category !== request.reviewed_value.category ||
        event.reviewed_value.requirement_type !== request.reviewed_value.requirement_type))
  )
    throw new ReviewSubmissionError('TenderCite returned an invalid review confirmation.');
  return event;
}
