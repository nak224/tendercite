import { useRef, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { Async, Empty } from '../components/ui';
import { ManualRequirementFields } from '../components/ManualRequirementFields';
import {
  buildIntake,
  intakeDocuments,
  sendIntake,
  IntakeInputError,
  IntakeRequestError,
  type CuratedTenderIntake,
  type IntakeSummary,
  type RequirementDraft,
} from '../api/tenderciteIntake';

function blank(item_id: string): RequirementDraft {
  return {
    item_id,
    statement: '',
    category: '',
    requirement_type: '',
    evidence: [{ document_id: '', page_number: '1', quote: '' }],
    interpretation_notes: '',
  };
}
type Validated = { intake: CuratedTenderIntake; summary: IntakeSummary };

export function ManualIntake() {
  const client = useQueryClient();
  const documents = useQuery({
    queryKey: ['tendercite', 'documents'],
    queryFn: ({ signal }) => intakeDocuments(signal),
    retry: false,
    gcTime: 0,
  });
  const nextId = useRef(2);
  const revision = useRef(0);
  const running = useRef(false);
  const [purpose, setPurpose] = useState('');
  const [requirements, setRequirements] = useState<RequirementDraft[]>(() => [blank('item-1')]);
  const [validated, setValidated] = useState<Validated | null>(null);
  const [confirmed, setConfirmed] = useState(false);
  const [saved, setSaved] = useState<IntakeSummary | null>(null);
  const [busy, setBusy] = useState<'validate' | 'commit' | null>(null);
  const [notice, setNotice] = useState('');
  const [failure, setFailure] = useState<{
    message: string;
    saving: boolean;
    analysisId?: string;
  } | null>(null);
  const sourcesMatch =
    (documents.isSuccess &&
      validated?.intake.documents.every((ref) =>
        documents.data?.some(
          (doc) => doc.id === ref.document_id && doc.sha256 === ref.expected_pdf_sha256,
        ),
      )) ??
    false;

  function edit(change: () => void) {
    revision.current++;
    change();
    setNotice(validated || saved ? 'Form changed. Validate the current requirements again.' : '');
    setValidated(null);
    setConfirmed(false);
    setSaved(null);
    setFailure(null);
  }
  async function act(operation: 'validate' | 'commit') {
    if (
      running.current ||
      (operation === 'commit' && (!validated || !confirmed || !sourcesMatch || saved))
    )
      return;
    const version = revision.current;
    const previous = validated;
    running.current = true;
    setBusy(operation);
    setFailure(null);
    setNotice('');
    setConfirmed(false);
    try {
      const current = buildIntake(purpose, requirements, documents.data ?? []);
      if (operation === 'commit' && JSON.stringify(current) !== JSON.stringify(previous!.intake))
        throw new IntakeInputError('The form or sources changed. Validate again before saving.');
      setValidated(null);
      const payload = operation === 'commit' ? previous!.intake : current;
      const summary = await sendIntake(operation, payload, previous?.summary);
      if (version !== revision.current) {
        setNotice(
          'The form changed while the request was pending. Validate the current draft again.',
        );
        if (operation === 'commit')
          setFailure({
            message: 'Inspect the earlier selection before another save.',
            saving: true,
            analysisId: summary.analysis_id,
          });
        return;
      }
      if (operation === 'validate') setValidated({ intake: payload, summary });
      else {
        setSaved(summary);
        await client.invalidateQueries({
          predicate: (query) =>
            query.queryKey[0] === 'tendercite' &&
            typeof query.queryKey[1] === 'string' &&
            (query.queryKey[1].startsWith('findings') ||
              query.queryKey[1] === `analyses/${summary.analysis_id}`),
        });
      }
    } catch (error) {
      setValidated(null);
      setFailure({
        message:
          error instanceof IntakeInputError || error instanceof IntakeRequestError
            ? error.message
            : 'Cannot prepare this intake. Check the form and source catalogue.',
        saving: operation === 'commit',
        analysisId: operation === 'commit' ? previous?.summary.analysis_id : undefined,
      });
    } finally {
      running.current = false;
      setBusy(null);
    }
  }
  return (
    <>
      <section className="panel">
        <h2>Manual Requirement Intake</h2>
        <p>
          Enter candidate requirements yourself. Select uploaded PDFs and inspect their stored text;
          quotations are not generated or repaired.
        </p>
        <p>
          Quote verification establishes occurrence, not interpretation correctness, qualification
          or bidder eligibility. New findings start UNREVIEWED; saving does not create or change
          reviews.
        </p>
        <Async query={documents}>
          {(sources) =>
            sources.length ? (
              <form
                onSubmit={(event) => {
                  event.preventDefault();
                  void act('validate');
                }}
              >
                <fieldset disabled={busy !== null}>
                  <label>
                    Intake purpose
                    <textarea
                      required
                      maxLength={2000}
                      rows={2}
                      value={purpose}
                      onChange={(e) => edit(() => setPurpose(e.target.value))}
                    />
                  </label>
                  {requirements.map((value, index) => (
                    <ManualRequirementFields
                      key={value.item_id}
                      value={value}
                      number={index + 1}
                      documents={sources}
                      removable={requirements.length > 1}
                      onChange={(updated) =>
                        edit(() =>
                          setRequirements((previous) =>
                            previous.map((row) =>
                              row.item_id === updated.item_id ? updated : row,
                            ),
                          ),
                        )
                      }
                      onRemove={() =>
                        edit(() =>
                          setRequirements((previous) =>
                            previous.filter((row) => row.item_id !== value.item_id),
                          ),
                        )
                      }
                    />
                  ))}
                  <div className="actions">
                    <button
                      type="button"
                      disabled={requirements.length >= 100}
                      onClick={() => {
                        const added = blank(`item-${nextId.current++}`);
                        edit(() => setRequirements((previous) => [...previous, added]));
                      }}
                    >
                      Add requirement
                    </button>
                    <button type="submit">Validate</button>
                  </div>
                </fieldset>
              </form>
            ) : (
              <Empty>
                No uploaded PDFs available. Upload a permitted PDF from All tenders first.
              </Empty>
            )
          }
        </Async>
      </section>
      {busy && (
        <p role="status">
          {busy === 'validate'
            ? 'Validating source evidence…'
            : 'Saving validated candidate requirements…'}
        </p>
      )}
      {notice && <p role="status">{notice}</p>}
      {failure && (
        <div role="alert" className="notice danger">
          <p>{failure.message}</p>
          {failure.saving && (
            <>
              <p>
                Save outcome is not confirmed. A failed response does not prove nothing was saved.
                Check the analysis before explicitly revalidating and saving again.
              </p>
              {failure.analysisId && (
                <Link
                  to={`/tenders/analyses/${encodeURIComponent(failure.analysisId)}`}
                  target="_blank"
                  rel="noopener noreferrer"
                >
                  Inspect possible analysis (opens new tab)
                </Link>
              )}
            </>
          )}
        </div>
      )}
      {validated && sourcesMatch && (
        <section className="panel" aria-label="Validated intake">
          <h3>Source validation passed</h3>
          <p>
            {validated.summary.finding_count} requirements · {validated.summary.source_count}{' '}
            documents · {validated.summary.quotation_count} quotations
          </p>
          <p>Validation did not save an analysis. Inspect your interpretations before saving.</p>
          <label>
            <input
              type="checkbox"
              checked={confirmed}
              onChange={(e) => setConfirmed(e.target.checked)}
              disabled={busy !== null}
            />
            I confirm these are manually entered candidate requirements and want to save them.
          </label>
          <button
            type="button"
            disabled={!confirmed || busy !== null}
            onClick={() => void act('commit')}
          >
            Save requirements
          </button>
        </section>
      )}
      {validated && !sourcesMatch && (
        <p role="alert">The source catalogue changed. Validate again before saving.</p>
      )}
      {saved && (
        <section className="panel" aria-label="Saved intake">
          <h3>{saved.status === 'CREATED' ? 'Requirements saved' : 'Existing intake reused'}</h3>
          <p>
            {saved.status === 'CREATED'
              ? 'New findings are UNREVIEWED.'
              : 'ALREADY_PRESENT: existing reviews remain unchanged.'}
          </p>
          <div className="actions">
            <Link to={`/tenders/analyses/${encodeURIComponent(saved.analysis_id)}`}>
              Open analysis
            </Link>
            <Link
              to={`/tenders/analyses/${encodeURIComponent(saved.analysis_id)}#current-findings`}
            >
              Current findings
            </Link>
          </div>
        </section>
      )}
    </>
  );
}
