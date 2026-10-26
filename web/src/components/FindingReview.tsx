import { useRef, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import type { TenderFinding } from '../api/tendercite';
import { categories, requirementTypes } from '../api/tenderciteIntake';
import {
  reviewActions,
  reviewHistory,
  reviewRequest,
  saveReview,
  ReviewInputError,
  ReviewSubmissionError,
  type ReviewAction,
  type ReviewedValue,
} from '../api/tenderciteReviews';
import { Empty } from './ui';

export function FindingReview({
  finding,
  refreshFinding,
  findingCurrent,
}: {
  finding: TenderFinding;
  findingCurrent: boolean;
  refreshFinding: () => Promise<unknown>;
}) {
  const history = useQuery({
    queryKey: ['tendercite', `findings/${finding.id}/reviews`],
    queryFn: ({ signal }) => reviewHistory(finding.id, signal),
    retry: false,
    gcTime: 0,
  });
  const [action, setAction] = useState<ReviewAction | ''>('');
  const [value, setValue] = useState<ReviewedValue>(finding.effective_value as ReviewedValue);
  const [valueEdited, setValueEdited] = useState(false);
  function changeValue(next: ReviewedValue) {
    setValueEdited(true);
    setValue(next);
  }
  const [comment, setComment] = useState('');
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  const [failure, setFailure] = useState('');
  const [reloadFailed, setReloadFailed] = useState(false);
  const running = useRef(false);

  async function reload() {
    const outcomes = await Promise.allSettled([
      refreshFinding(),
      history.refetch({ throwOnError: true }),
    ]);
    setReloadFailed(outcomes.some((outcome) => outcome.status === 'rejected'));
  }
  async function refresh() {
    if (running.current) return;
    running.current = true;
    setBusy(true);
    try {
      await reload();
    } finally {
      running.current = false;
      setBusy(false);
    }
  }
  async function submit() {
    if (running.current || !action || !history.isSuccess || !findingCurrent || reloadFailed) return;
    let request;
    try {
      request = reviewRequest(action, value, comment);
    } catch (error) {
      setFailure(error instanceof ReviewInputError ? error.message : 'Invalid review input.');
      return;
    }
    running.current = true;
    setBusy(true);
    setMessage('');
    setFailure('');
    try {
      const event = await saveReview(finding.id, request);
      setMessage(
        `Review saved: ${event.action}. This is an interpretation review, not fulfillment or compliance.`,
      );
    } catch (error) {
      setFailure(
        `${error instanceof ReviewSubmissionError ? error.message : 'Review response unavailable.'} Save outcome is uncertain; a failed response does not prove nothing was saved. Inspect the refreshed finding and history before explicitly trying again. No automatic retry was made.`,
      );
    } finally {
      await reload(); // Read authoritative state even when a POST response was lost.
      running.current = false;
      setBusy(false);
    }
  }
  return (
    <>
      <section className="panel" aria-label="Human finding review">
        <h2>Human finding review</h2>
        <p>
          Source quotation verification checks text occurrence. Human review records an
          interpretation; CONFIRMED does not mean FULFILLED, legally correct, compliant or eligible.
          No qualification or Go/No-Go decision is made here.
        </p>
        <p>
          The backend has no optimistic concurrency check. Another operator can review this finding
          before your save; Confirm and Reject apply to the server's current interpretation. Reload
          and inspect current state before saving. Reloading preserves your form edits.
        </p>
        <button type="button" disabled={busy} onClick={() => void refresh()}>
          Reload finding and history
        </button>
        <form
          onSubmit={(e) => {
            e.preventDefault();
            void submit();
          }}
        >
          <fieldset disabled={busy}>
            <label>
              Review action
              <select
                required
                value={action}
                onChange={(e) => {
                  const next = e.target.value as ReviewAction;
                  if (next === 'MODIFY' && !valueEdited)
                    setValue(finding.effective_value as ReviewedValue);
                  setAction(next);
                }}
              >
                <option value="">Select action</option>
                {reviewActions.map((option) => (
                  <option key={option}>{option}</option>
                ))}
              </select>
            </label>
            {action === 'MODIFY' && (
              <>
                <label>
                  Reviewed requirement statement
                  <textarea
                    required
                    maxLength={4000}
                    rows={3}
                    value={value.statement}
                    onChange={(e) => changeValue({ ...value, statement: e.target.value })}
                  />
                </label>
                <div className="form-grid">
                  <label>
                    Reviewed category
                    <select
                      value={value.category}
                      onChange={(e) =>
                        changeValue({
                          ...value,
                          category: e.target.value as ReviewedValue['category'],
                        })
                      }
                    >
                      {categories.map((category) => (
                        <option key={category}>{category}</option>
                      ))}
                    </select>
                  </label>
                  <label>
                    Reviewed requirement type
                    <select
                      value={value.requirement_type}
                      onChange={(e) =>
                        changeValue({
                          ...value,
                          requirement_type: e.target.value as ReviewedValue['requirement_type'],
                        })
                      }
                    >
                      {requirementTypes.map((type) => (
                        <option key={type}>{type}</option>
                      ))}
                    </select>
                  </label>
                </div>
              </>
            )}
            <label>
              Review comment (optional)
              <textarea
                rows={2}
                maxLength={4000}
                value={comment}
                onChange={(e) => setComment(e.target.value)}
              />
            </label>
            <button
              type="submit"
              disabled={!action || !history.isSuccess || !findingCurrent || reloadFailed}
            >
              Save Review
            </button>
          </fieldset>
        </form>
        {busy && <p role="status">Saving or reloading review state…</p>}
        {message && <p role="status">{message}</p>}
        {failure && <p role="alert">{failure}</p>}
        {reloadFailed && (
          <p role="alert">
            Could not reload current finding and history. Displayed state may be stale; reload
            before another review.
          </p>
        )}
      </section>
      <section className="panel" aria-label="Review history">
        <h2>Review history</h2>
        {history.isPending && <p role="status">Loading review history…</p>}
        {history.isError && (
          <p role="alert">Could not load review history. Reload before saving.</p>
        )}
        {history.data &&
          (history.data.length ? (
            <ol>
              {history.data.map((event) => (
                <li key={event.id}>
                  <p>
                    {event.action} · {event.created_at} · Previous status: {event.previous_status}
                  </p>
                  <p>
                    Previous interpretation: {event.previous_value.statement} ·{' '}
                    {event.previous_value.category} · {event.previous_value.requirement_type}
                  </p>
                  <p>
                    Reviewed interpretation: {event.reviewed_value.statement} ·{' '}
                    {event.reviewed_value.category} · {event.reviewed_value.requirement_type}
                  </p>
                  {event.comment && <p>Comment: {event.comment}</p>}
                </li>
              ))}
            </ol>
          ) : (
            <Empty>No review events. This finding has not been reviewed.</Empty>
          ))}
      </section>
    </>
  );
}
