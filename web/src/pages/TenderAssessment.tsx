import { useRef, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Link, useParams } from 'react-router-dom';
import type { TenderFinding } from '../api/tendercite';
import {
  assessmentContext,
  assessmentEligible,
  assessmentRequest,
  assessmentStatuses,
  saveAssessment,
  AssessmentInputError,
  AssessmentSaveError,
  type AssessmentRequest,
  type AssessmentRow,
} from '../api/tenderciteAssessments';
import { Async, Empty } from '../components/ui';

function FindingSummary({ finding }: { finding: TenderFinding }) {
  return (
    <>
      <h3>{finding.effective_value.statement}</h3>
      <p>
        {finding.effective_value.category} · {finding.effective_value.requirement_type}
      </p>
      <p>
        Review: {finding.review_status} · Grounding: {finding.grounding_status}
      </p>
      <Link to={`/tenders/findings/${encodeURIComponent(finding.id)}`}>
        Finding details and source evidence
      </Link>
      <ul>
        {finding.evidence.map((e, index) => (
          <li key={index}>
            <Link
              to={`/tenders/documents/${encodeURIComponent(e.document_id)}/pages/${e.page_number}`}
            >
              Document {e.document_id} · physical PDF page {e.page_number}
            </Link>
          </li>
        ))}
      </ul>
    </>
  );
}
function AssessmentEditor({
  finding,
  row,
  disabled,
  save,
}: {
  finding: TenderFinding;
  row: AssessmentRow;
  disabled: boolean;
  save: (id: string, request: AssessmentRequest) => Promise<void>;
}) {
  // Never seed a new judgment from a previous stored assessment.
  const [status, setStatus] = useState('');
  const [rationale, setRationale] = useState('');
  const [error, setError] = useState('');
  return (
    <article className="panel" aria-label={`Assessment for ${finding.id}`}>
      <FindingSummary finding={finding} />
      <p>Current assessment: {row.status}</p>
      <p>Current rationale: {row.rationale ?? 'No manual rationale recorded.'}</p>
      {finding.grounding_status !== 'VERIFIED_QUOTE' && (
        <p className="notice muted">
          Source grounding is not verified. Manual assessment does not repair source evidence.
        </p>
      )}
      <form
        onSubmit={(event) => {
          event.preventDefault();
          if (disabled) return;
          try {
            const request = assessmentRequest(status, rationale);
            setError('');
            void save(finding.id, request);
          } catch (failure) {
            setError(
              failure instanceof AssessmentInputError
                ? failure.message
                : 'Invalid assessment input.',
            );
          }
        }}
      >
        <fieldset disabled={disabled}>
          <label>
            Assessment status
            <select required value={status} onChange={(e) => setStatus(e.target.value)}>
              <option value="">Select an operator judgment</option>
              {assessmentStatuses.map((option) => (
                <option key={option}>{option}</option>
              ))}
            </select>
          </label>
          <label>
            Assessment rationale
            <textarea
              required
              maxLength={4000}
              rows={3}
              value={rationale}
              onChange={(e) => setRationale(e.target.value)}
            />
          </label>
          <button type="submit">Save Assessment</button>
        </fieldset>
      </form>
      {error && <p role="alert">{error}</p>}
    </article>
  );
}
export function TenderAssessment() {
  const { id = '' } = useParams();
  const client = useQueryClient();
  const readSequence = useRef(0);
  const query = useQuery({
    queryKey: ['tendercite', `go-no-go?analysis_run_id=${encodeURIComponent(id)}`],
    queryFn: async ({ signal }) => ({
      ...(await assessmentContext(id, signal)),
      readVersion: ++readSequence.current,
    }),
    retry: false,
    gcTime: 0,
  });
  const running = useRef(false);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  const [failure, setFailure] = useState('');
  async function reload() {
    // Refetch both current findings and the review-bound assessment matrix.
    await Promise.allSettled([
      query.refetch({ throwOnError: true }),
      client.invalidateQueries({
        predicate: (entry) =>
          entry.queryKey[0] === 'tendercite' &&
          typeof entry.queryKey[1] === 'string' &&
          (entry.queryKey[1].startsWith('findings') ||
            entry.queryKey[1] === `analyses/${encodeURIComponent(id)}`),
      }),
    ]);
  }
  async function refresh() {
    if (running.current) return;
    running.current = true;
    setBusy(true);
    setMessage('');
    try {
      await reload();
    } finally {
      running.current = false;
      setBusy(false);
    }
  }
  async function save(findingId: string, request: AssessmentRequest) {
    if (
      running.current ||
      query.isError ||
      query.isFetching ||
      !query.data?.findings.some((f) => f.id === findingId && assessmentEligible(f))
    )
      return;
    running.current = true;
    setBusy(true);
    setMessage('');
    setFailure('');
    try {
      const row = await saveAssessment(findingId, request);
      setMessage(
        `Assessment saved for ${row.finding_id}: ${row.status}. This records an operator judgment only.`,
      );
    } catch (error) {
      setFailure(
        `${error instanceof AssessmentSaveError ? error.message : 'Assessment response unavailable.'} Save is not confirmed; persistence may be uncertain. A failed response does not prove nothing was saved. Inspect the refreshed server state before an explicit retry. No automatic retry was made.`,
      );
    } finally {
      await reload();
      running.current = false;
      setBusy(false);
    }
  }
  const data = query.data;
  if (!data) return <Async query={query}>{() => null}</Async>;
  const eligible = data.findings.filter(assessmentEligible);
  const unreviewed = data.findings.filter((f) => f.review_status === 'UNREVIEWED');
  const rejected = data.findings.filter((f) => f.review_status === 'REJECTED');
  return (
    <>
      <section className="panel">
        <h2>Go/No-Go Assessment</h2>
        <Link to={`/tenders/analyses/${encodeURIComponent(id)}`}>Analysis {id}</Link>
        <p>Manual assessment matrix only: no automatic overall Go/No-Go verdict is calculated.</p>
        <p>
          Human review records an interpretation. Manual assessment records an operator's judgment.
          Neither source quotation verification nor review/assessment automatically verifies bidder
          eligibility or legal compliance.
        </p>
        <p>
          A new accepted review invalidates the previous assessment and returns it to
          CLARIFICATION_NEEDED. Rejected findings are excluded. Current values always come from the
          server. Reloading current data clears unsaved assessment drafts; previous judgments are
          never automatically restored.
        </p>
        <p>
          The backend has no assessment revision precondition. Another operator can review or assess
          between inspection and saving. Reload and inspect current state before an explicit save.
        </p>
        <button type="button" disabled={busy || query.isFetching} onClick={() => void refresh()}>
          Reload assessment matrix
        </button>
      </section>
      {busy && <p role="status">Saving or reloading assessment state…</p>}
      {message && <p role="status">{message}</p>}
      {failure && <p role="alert">{failure}</p>}
      {query.isError && (
        <p role="alert">
          Could not reload the current matrix. Displayed data may be stale; reload before saving.
        </p>
      )}
      <section aria-label="Eligible reviewed findings">
        <h2>Reviewed findings eligible for manual assessment ({eligible.length})</h2>
        {eligible.length ? (
          eligible.map((finding) => (
            <AssessmentEditor
              key={`${finding.id}:${data.readVersion}`}
              finding={finding}
              row={data.rows.find((row) => row.finding_id === finding.id)!}
              disabled={busy || query.isFetching || query.isError}
              save={save}
            />
          ))
        ) : (
          <Empty>No reviewed findings eligible for manual assessment.</Empty>
        )}
      </section>
      <section className="panel" aria-label="Findings requiring human review">
        <h2>UNREVIEWED — human review required ({unreviewed.length})</h2>
        {unreviewed.map((finding) => (
          <article key={finding.id}>
            <FindingSummary finding={finding} />
          </article>
        ))}
      </section>
      <section className="panel" aria-label="Rejected findings excluded">
        <h2>REJECTED — excluded from assessment ({rejected.length})</h2>
        {rejected.map((finding) => (
          <article key={finding.id}>
            <FindingSummary finding={finding} />
          </article>
        ))}
      </section>
    </>
  );
}
