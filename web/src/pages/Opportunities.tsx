import { useEffect, useRef, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { Async, Empty } from '../components/ui';
import { OpportunityForm } from '../components/OpportunityForm';
import { SavedNativeSelection } from '../components/SavedNativeSelection';
import { tenderciteConfigured } from '../api/tendercite';
import {
  emptyOpportunity,
  getOpportunity,
  listOpportunities,
  saveOpportunity,
  OpportunityInputError,
  OpportunityRequestError,
  type NativeOpportunity,
  type OpportunityRecord,
} from '../api/opportunities';

function OpportunityWorkspace({ initialRecord }: { initialRecord?: OpportunityRecord }) {
  const [offset, setOffset] = useState(0);
  const [id, setId] = useState(initialRecord?.opportunity_id ?? '');
  const [record, setRecord] = useState<OpportunityRecord | null>(initialRecord ?? null);
  const [initial, setInitial] = useState<NativeOpportunity | null>(
    initialRecord?.opportunity ?? null,
  );
  const [version, setVersion] = useState(0);
  const [observed, setObserved] = useState<OpportunityRecord | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [dirty, setDirty] = useState(false);
  const unsaved = useRef(false);
  const running = useRef(false);
  const alive = useRef(true);
  const read = useRef<AbortController | null>(null);
  useEffect(() => {
    alive.current = true;
    function beforeUnload(event: BeforeUnloadEvent) {
      if (unsaved.current) {
        event.preventDefault();
        event.returnValue = '';
      }
    }
    window.addEventListener('beforeunload', beforeUnload);
    return () => {
      alive.current = false;
      read.current?.abort();
      window.removeEventListener('beforeunload', beforeUnload);
    };
  }, []);
  const listing = useQuery({
    queryKey: ['tendercite', 'native-opportunities', offset],
    queryFn: ({ signal }) => listOpportunities(offset, signal),
    retry: false,
    gcTime: 0,
    refetchOnWindowFocus: false,
  });
  function load(value: OpportunityRecord) {
    unsaved.current = false;
    setDirty(false);
    setId(value.opportunity_id);
    setRecord(value);
    setInitial(value.opportunity);
    setObserved(null);
    setVersion((v) => v + 1);
  }
  async function open(opportunityId: string) {
    if (running.current || !discardAllowed()) return;
    running.current = true;
    setBusy(true);
    setError('');
    setNotice('');
    const controller = new AbortController();
    read.current = controller;
    try {
      const value = await getOpportunity(opportunityId, controller.signal);
      if (alive.current) load(value);
    } catch (failure) {
      if (alive.current)
        setError(
          failure instanceof OpportunityRequestError ? failure.message : 'Cannot load opportunity.',
        );
    } finally {
      running.current = false;
      if (alive.current) setBusy(false);
    }
  }
  function discardAllowed() {
    return !unsaved.current || window.confirm('Discard unsaved opportunity changes?');
  }
  function markDirty() {
    unsaved.current = true;
    setDirty(true);
    setNotice('');
  }
  async function checkCurrent() {
    const controller = new AbortController();
    read.current = controller;
    try {
      const value = await getOpportunity(id, controller.signal);
      if (alive.current) setObserved(value);
    } catch {
      if (alive.current)
        setNotice(
          'Current saved version could not be read. Your form is retained; check again before choosing whether to save.',
        );
    }
  }
  async function inspectCurrent() {
    if (running.current || !id) return;
    running.current = true;
    setBusy(true);
    try {
      await checkCurrent();
    } finally {
      running.current = false;
      if (alive.current) setBusy(false);
    }
  }
  async function save(opportunity: NativeOpportunity) {
    if (running.current || !id) return;
    running.current = true;
    setBusy(true);
    setError('');
    setNotice('');
    setObserved(null);
    try {
      const result = await saveOpportunity(id, opportunity, record?.revision);
      if (alive.current) {
        load(result.record);
        setNotice(
          `Server confirmed ${result.status}. Opportunity saved at revision ${result.record.revision}. Details remain unverified.`,
        );
      }
    } catch (failure) {
      if (alive.current) {
        setError(
          failure instanceof OpportunityInputError
            ? failure.message
            : `${failure instanceof OpportunityRequestError ? failure.message : 'Opportunity save response unavailable.'} Save not confirmed: server state may have changed. No automatic retry was made. Your form is retained.`,
        );
        if (!(failure instanceof OpportunityInputError)) await checkCurrent();
      }
    } finally {
      if (alive.current) {
        await listing.refetch();
        setBusy(false);
      }
      running.current = false;
    }
  }
  return (
    <>
      <section className="panel" aria-label="Saved opportunities">
        <h2>Your opportunities</h2>
        <p>
          Local references to externally sourced projects, tenders and other business opportunities.
          No document, profile, workforce snapshot or external integration is required.
        </p>
        <p>
          Opportunity storage is local-development-only. If unavailable, explicitly enable
          TENDERCITE_OPPORTUNITIES_API_ENABLED and restart the backend. The flag is not
          authentication.
        </p>
        <button
          type="button"
          disabled={busy}
          onClick={() => {
            if (running.current || !discardAllowed()) return;
            unsaved.current = false;
            setDirty(false);
            setId(crypto.randomUUID());
            setRecord(null);
            setInitial(emptyOpportunity());
            setVersion((v) => v + 1);
            setError('');
            setNotice('');
            setObserved(null);
          }}
        >
          Create Opportunity
        </button>
        <button
          type="button"
          disabled={busy || listing.isFetching}
          onClick={() => void listing.refetch()}
        >
          Reload opportunity list
        </button>
        <Async query={listing}>
          {(data) => (
            <>
              {!data.opportunities.length && <Empty>No saved opportunities yet.</Empty>}
              <ul>
                {data.opportunities.map((p) => (
                  <li key={p.opportunity_id}>
                    <button
                      type="button"
                      disabled={busy}
                      onClick={() => void open(p.opportunity_id)}
                    >
                      Open {p.title}
                    </button>{' '}
                    · {p.opportunity_type} · {p.organization || 'Organization not specified'}
                    {p.deadline && (
                      <>
                        {' '}
                        · Deadline: <time dateTime={p.deadline}>{p.deadline}</time>
                      </>
                    )}{' '}
                    · Revision {p.revision}
                    <div className="actions">
                      <Link to={`/matching?opportunity_id=${p.opportunity_id}`}>
                        Compare this opportunity
                      </Link>
                      <Link to={`/pipeline?opportunity_id=${p.opportunity_id}`}>
                        Track this opportunity
                      </Link>
                    </div>
                  </li>
                ))}
              </ul>
              <div className="actions">
                <button
                  type="button"
                  disabled={busy || offset === 0}
                  onClick={() => setOffset((n) => Math.max(0, n - 50))}
                >
                  Previous opportunities
                </button>
                <button
                  type="button"
                  disabled={busy || data.next_offset === null}
                  onClick={() => {
                    if (data.next_offset !== null) setOffset(data.next_offset);
                  }}
                >
                  Next opportunities
                </button>
              </div>
            </>
          )}
        </Async>
      </section>
      {busy && <p role="status">Saving or loading opportunity…</p>}
      {notice && <p role="status">{notice}</p>}
      {error && <p role="alert">{error}</p>}
      {initial && (
        <section className="panel" aria-label="Opportunity editor">
          <h2>{record ? 'Edit opportunity' : 'New opportunity'}</h2>
          {record && (
            <p>
              Saved revision: {record.revision} · Last updated: {record.updated_at}
            </p>
          )}
          {dirty && (
            <p role="status">
              Unsaved changes. Save before leaving this page; switching opportunities requires
              confirmation.
            </p>
          )}
          {record?.opportunity.external_source && (
            <aside aria-label="TED import provenance">
              <p>
                Imported from TED publication {record.opportunity.external_source.external_id} at{' '}
                {record.opportunity.external_source.retrieved_at}. This source attribution is
                retained through local edits. Metadata and qualifications are NOT_VERIFIED.
              </p>
              <a
                href={record.opportunity.external_source.original_url}
                target="_blank"
                rel="noopener noreferrer"
                referrerPolicy="no-referrer"
              >
                Original TED publication
              </a>
              {record.opportunity.external_source.mapping_warnings.length > 0 && (
                <p>
                  Import mapping notes:{' '}
                  {record.opportunity.external_source.mapping_warnings.join(', ')}. Check the
                  original notice.
                </p>
              )}
            </aside>
          )}
          {record?.opportunity.source_url && (
            <p>
              Stored external reference:{' '}
              <a
                href={record.opportunity.source_url}
                target="_blank"
                rel="noopener noreferrer"
                referrerPolicy="no-referrer"
              >
                Open original opportunity
              </a>{' '}
              · NOT_VERIFIED
            </p>
          )}
          <OpportunityForm
            key={`${id}:${version}`}
            initial={initial}
            disabled={busy}
            save={save}
            onDirty={markDirty}
          />
          <button type="button" disabled={busy} onClick={() => void inspectCurrent()}>
            Check current saved version
          </button>
          {observed && (
            <aside aria-label="Current saved version">
              <p>
                The server currently has revision {observed.revision} of{' '}
                {observed.opportunity.title}. Unsaved form changes have not been applied to it.
              </p>
              <button
                type="button"
                disabled={busy}
                onClick={() => {
                  if (!discardAllowed()) return;
                  load(observed);
                  setError('');
                  setNotice('Loaded the current saved version. Make changes and save explicitly.');
                }}
              >
                Load current saved version (discard my edits)
              </button>
            </aside>
          )}
        </section>
      )}
      <p>
        Opportunities are locally saved external references, not listings hosted or published by
        TenderCite. Their details, requirements and deadlines are not independently verified. A
        PUBLIC_TENDER opportunity is not an analyzed tender or proof of eligibility. Saving does not
        automatically compare or track it. Use <Link to="/matching">Opportunity Matching</Link> for
        explicit skill-label comparison or <Link to="/pipeline">Pipeline</Link> for manual tracking.
      </p>
    </>
  );
}
export function Opportunities() {
  return (
    <>
      <div className="page-heading">
        <div>
          <h1>Opportunities</h1>
          <p>Your local references to external projects and tenders.</p>
        </div>
      </div>
      {tenderciteConfigured() ? (
        <SavedNativeSelection mode="opportunities">
          {(context) => <OpportunityWorkspace initialRecord={context.opportunityRecord} />}
        </SavedNativeSelection>
      ) : (
        <Empty>
          TenderCite is not configured. Set TENDERCITE_PROXY_TARGET for the Vite development server
          and restart it.
        </Empty>
      )}
    </>
  );
}
