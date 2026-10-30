import { useEffect, useRef, useState } from 'react';
import { Link, useBlocker } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { tenderciteConfigured } from '../api/tendercite';
import { listOpportunities } from '../api/opportunities';
import {
  addPipelineEntry,
  getPipelineEntry,
  listPipeline,
  updatePipelineEntry,
  pipelineStages,
  PipelineInputError,
  PipelineRequestError,
  type PipelineEntry,
  type PipelineStage,
  type PipelineValues,
} from '../api/pipeline';
import { Async, Empty } from '../components/ui';
import { PipelineForm } from '../components/PipelineForm';

export function Pipeline() {
  return (
    <>
      <section className="panel">
        <h1>Pipeline</h1>
        <p>
          Manually track external projects and tenders.{' '}
          <Link to="/opportunities">Opportunities</Link> supplies the original details;{' '}
          <Link to="/matching">Opportunity Matching</Link> compares declarations independently.
          Matching is optional and never adds or advances an entry.
        </p>
        <p>
          Stages are operator declarations. SUBMITTED sends no application. WON and LOST are not
          independently verified outcomes. No external synchronization, reminders, qualification or
          Go/No-Go decision occurs.
        </p>
      </section>
      {tenderciteConfigured() ? (
        <PipelineWorkspace />
      ) : (
        <Empty>
          TenderCite is not configured. Set the fixed local development proxy and restart Vite.
        </Empty>
      )}
    </>
  );
}
function PipelineWorkspace() {
  const [stage, setStage] = useState<PipelineStage | ''>('');
  const [offset, setOffset] = useState(0);
  const [opportunityOffset, setOpportunityOffset] = useState(0);
  const [opportunityId, setOpportunityId] = useState('');
  const [creationId, setCreationId] = useState('');
  const [selected, setSelected] = useState<PipelineEntry | null>(null);
  const [observed, setObserved] = useState<PipelineEntry | null>(null);
  const [found, setFound] = useState<PipelineEntry | null>(null);
  const [version, setVersion] = useState(0);
  const [dirty, setDirty] = useState(false);
  const [busy, setBusy] = useState(false);
  const [needsRefresh, setNeedsRefresh] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const running = useRef(false),
    unsaved = useRef(false),
    alive = useRef(true);
  const read = useRef<AbortController | null>(null);
  const blocker = useBlocker(
    ({ currentLocation, nextLocation }) =>
      (unsaved.current || running.current) && currentLocation.key !== nextLocation.key,
  );
  useEffect(() => {
    alive.current = true;
    function beforeUnload(event: BeforeUnloadEvent) {
      if (unsaved.current || running.current) {
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
  const options = {
    retry: false,
    gcTime: 0,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
  } as const;
  const listing = useQuery({
    ...options,
    queryKey: ['tendercite', 'pipeline', stage, offset],
    queryFn: ({ signal }) => listPipeline(offset, stage, signal),
  });
  const opportunities = useQuery({
    ...options,
    queryKey: ['tendercite', 'pipeline-opportunities', opportunityOffset],
    queryFn: ({ signal }) => listOpportunities(opportunityOffset, signal),
  });
  const existing = useQuery({
    ...options,
    queryKey: ['tendercite', 'pipeline-opportunity', opportunityId],
    enabled: !!opportunityId,
    queryFn: ({ signal }) => listPipeline(0, '', signal, opportunityId),
  });
  const duplicate = found ?? existing.data?.entries[0];
  function discardAllowed() {
    return !unsaved.current || window.confirm('Discard unsaved pipeline changes?');
  }
  function load(entry: PipelineEntry) {
    unsaved.current = false;
    setDirty(false);
    setSelected(entry);
    setObserved(null);
    setNeedsRefresh(false);
    setVersion((v) => v + 1);
  }
  async function open(id: string) {
    if (running.current || !discardAllowed()) return;
    running.current = true;
    setBusy(true);
    setError('');
    setNotice('');
    const controller = new AbortController();
    read.current = controller;
    try {
      const entry = await getPipelineEntry(id, controller.signal);
      if (alive.current) load(entry);
    } catch (failure) {
      if (alive.current)
        setError(
          failure instanceof PipelineRequestError ? failure.message : 'Cannot load pipeline entry.',
        );
    } finally {
      running.current = false;
      if (alive.current) setBusy(false);
    }
  }
  async function checkCurrent(id: string) {
    const controller = new AbortController();
    read.current = controller;
    try {
      const entry = await getPipelineEntry(id, controller.signal);
      if (alive.current) setObserved(entry);
    } catch {
      if (alive.current)
        setNotice(
          'Current saved entry could not be read. Your form is retained. Check again before deciding what to do.',
        );
    }
  }
  async function refreshSelected() {
    if (running.current || !selected) return;
    running.current = true;
    setBusy(true);
    try {
      await checkCurrent(selected.record.pipeline_id);
    } finally {
      running.current = false;
      if (alive.current) setBusy(false);
    }
  }
  async function add() {
    if (
      running.current ||
      !creationId ||
      !opportunityId ||
      !existing.isSuccess ||
      existing.isFetching ||
      duplicate
    )
      return;
    if (!discardAllowed()) return;
    running.current = true;
    setBusy(true);
    setError('');
    setNotice('');
    try {
      const result = await addPipelineEntry(creationId, opportunityId);
      if (alive.current) {
        if (result.status === 'CREATED') load(result.entry);
        else setFound(result.entry);
        setNotice(
          result.status === 'CREATED'
            ? 'Server confirmed CREATED. Tracking starts at SAVED; no external action was taken.'
            : 'Server confirmed ALREADY_PRESENT. Open the existing entry; nothing was reset.',
        );
      }
    } catch (failure) {
      if (alive.current) {
        setError(
          `${failure instanceof PipelineInputError || failure instanceof PipelineRequestError ? failure.message : 'Pipeline creation response unavailable.'} Creation not confirmed: an entry may already exist. No automatic retry was made. Check the current pipeline before retrying.`,
        );
        const controller = new AbortController();
        read.current = controller;
        try {
          const current = await listPipeline(0, '', controller.signal, opportunityId);
          if (alive.current) setFound(current.entries[0] ?? null);
        } catch {
          /* Keep the safe original error. */
        }
      }
    } finally {
      if (alive.current) {
        await Promise.allSettled([
          listing.refetch(),
          ...(opportunityId ? [existing.refetch()] : []),
        ]);
        setBusy(false);
      }
      running.current = false;
    }
  }
  async function save(values: PipelineValues) {
    if (running.current || !selected || needsRefresh) return;
    running.current = true;
    setBusy(true);
    setError('');
    setNotice('');
    setObserved(null);
    try {
      const entry = await updatePipelineEntry(selected, values);
      if (alive.current) {
        load(entry);
        setNotice(
          `Server confirmed UPDATED at revision ${entry.record.revision}. Stage and outcome remain manual declarations.`,
        );
      }
    } catch (failure) {
      if (alive.current) {
        if (failure instanceof PipelineInputError) setError(failure.message);
        else {
          setError(
            `${failure instanceof PipelineRequestError ? failure.message : 'Pipeline save response unavailable.'} Save not confirmed: server state may have changed. No automatic retry was made. Your form is retained.`,
          );
          setNeedsRefresh(true);
          await checkCurrent(selected.record.pipeline_id);
        }
      }
    } finally {
      if (alive.current) {
        await Promise.allSettled([
          listing.refetch(),
          ...(opportunityId ? [existing.refetch()] : []),
        ]);
        setBusy(false);
      }
      running.current = false;
    }
  }
  return (
    <>
      {blocker.state === 'blocked' && (
        <aside role="alertdialog" aria-label="Unsaved pipeline changes" className="panel">
          <p>
            Unsaved pipeline edits will be discarded if you leave. Wait for any pending save before
            navigating.
          </p>
          <button autoFocus onClick={() => blocker.reset()}>
            Keep editing
          </button>
          <button disabled={busy} onClick={() => blocker.proceed()}>
            Discard changes and leave
          </button>
        </aside>
      )}
      <section className="panel" aria-label="Tracked opportunities">
        <h2>Tracked opportunities</h2>
        <p>
          Enable TENDERCITE_PIPELINE_API_ENABLED on the local synthetic backend. This separate flag
          is off by default and is not authentication.
        </p>
        <label>
          Filter by stage
          <select
            value={stage}
            disabled={busy}
            onChange={(e) => {
              setStage(e.target.value as PipelineStage | '');
              setOffset(0);
            }}
          >
            <option value="">All stages</option>
            {pipelineStages.map((s) => (
              <option key={s}>{s}</option>
            ))}
          </select>
        </label>
        <button disabled={busy || listing.isFetching} onClick={() => void listing.refetch()}>
          Reload pipeline list
        </button>
        <Async query={listing}>
          {(data) => (
            <>
              {!data.entries.length && <Empty>No tracked opportunities in this view.</Empty>}
              <ul className="card-list">
                {data.entries.map((entry) => (
                  <li key={entry.record.pipeline_id}>
                    <h3>
                      <button disabled={busy} onClick={() => void open(entry.record.pipeline_id)}>
                        Open {entry.opportunity.title}
                      </button>
                    </h3>
                    <p>
                      {entry.opportunity.organization || 'Organization not specified'} ·{' '}
                      {entry.opportunity.opportunity_type} · {entry.record.values.stage} · Priority{' '}
                      {entry.record.values.priority}
                    </p>
                    <p>
                      Submission deadline (date only):{' '}
                      {entry.opportunity.deadline ?? 'Not recorded'}
                    </p>
                    <p className="source-page-text">
                      Next action: {entry.record.values.next_action || 'Not recorded'}
                    </p>
                    <p>
                      Next-action due date:{' '}
                      {entry.record.values.next_action_due_date ?? 'Not recorded'}
                    </p>
                  </li>
                ))}
              </ul>
              <button disabled={busy || !offset} onClick={() => setOffset(offset - 50)}>
                Previous pipeline entries
              </button>
              <button
                disabled={busy || data.next_offset === null}
                onClick={() => setOffset(data.next_offset!)}
              >
                Next pipeline entries
              </button>
            </>
          )}
        </Async>
      </section>
      <section className="panel" aria-label="Add to pipeline">
        <h2>Add an existing opportunity</h2>
        <p>
          Create the original record in <Link to="/opportunities">Opportunities</Link>. Adding it
          here neither creates another opportunity nor submits anything externally.
        </p>
        <Async query={opportunities}>
          {(data) => (
            <>
              <label>
                Existing opportunity
                <select
                  disabled={busy}
                  value={opportunityId}
                  onChange={(e) => {
                    if (!discardAllowed()) return;
                    unsaved.current = false;
                    setDirty(false);
                    setSelected(null);
                    setObserved(null);
                    setFound(null);
                    setError('');
                    setNotice('');
                    setOpportunityId(e.target.value);
                    setCreationId(crypto.randomUUID());
                  }}
                >
                  <option value="">Select saved opportunity</option>
                  {data.opportunities.map((o) => (
                    <option key={o.opportunity_id} value={o.opportunity_id}>
                      {o.title} · {o.opportunity_type} · {o.organization || 'No organization'}
                    </option>
                  ))}
                </select>
              </label>
              <button
                disabled={busy || !opportunityOffset}
                onClick={() => {
                  setOpportunityOffset(opportunityOffset - 50);
                  setOpportunityId('');
                  setFound(null);
                }}
              >
                Previous opportunities
              </button>
              <button
                disabled={busy || data.next_offset === null}
                onClick={() => {
                  setOpportunityOffset(data.next_offset!);
                  setOpportunityId('');
                  setFound(null);
                }}
              >
                Next opportunities
              </button>
            </>
          )}
        </Async>
        {opportunityId && (
          <Async query={existing}>
            {() =>
              duplicate ? (
                <p>
                  Already tracked.{' '}
                  <button disabled={busy} onClick={() => void open(duplicate.record.pipeline_id)}>
                    Open existing pipeline entry
                  </button>
                </p>
              ) : (
                <p>This opportunity is not currently recorded in the pipeline.</p>
              )
            }
          </Async>
        )}
        <button
          disabled={
            busy || !opportunityId || !existing.isSuccess || existing.isFetching || !!duplicate
          }
          onClick={() => void add()}
        >
          Add to Pipeline
        </button>
      </section>
      {busy && <p role="status">Saving or loading pipeline…</p>}
      {notice && <p role="status">{notice}</p>}
      {error && <p role="alert">{error}</p>}
      {selected && (
        <section className="panel" aria-label="Pipeline editor">
          <h2>Manage {selected.opportunity.title}</h2>
          <p>
            Pipeline ID: {selected.record.pipeline_id} · Revision {selected.record.revision} ·
            Updated {selected.record.updated_at}
          </p>
          <p>
            Original opportunity: {selected.opportunity.organization} ·{' '}
            {selected.opportunity.opportunity_type} · Submission deadline (date only):{' '}
            {selected.opportunity.deadline ?? 'Not recorded'}. Edit source details in{' '}
            <Link to="/opportunities">Opportunities</Link>.
          </p>
          {dirty && <p role="status">Unsaved pipeline changes. Save explicitly before leaving.</p>}
          <PipelineForm
            key={`${selected.record.pipeline_id}:${version}`}
            initial={selected.record.values}
            busy={busy}
            blocked={needsRefresh}
            onDirty={() => {
              unsaved.current = true;
              setDirty(true);
              setNotice('');
            }}
            save={save}
          />
          <button disabled={busy} onClick={() => void refreshSelected()}>
            Check current saved entry
          </button>
          {needsRefresh && (
            <p>
              Review current server state and explicitly load it before another save. Your unsaved
              edits have not been merged or silently reapplied.
            </p>
          )}
          {observed && (
            <aside aria-label="Current saved pipeline entry">
              <p>
                Current server revision {observed.record.revision}: {observed.record.values.stage},{' '}
                {observed.record.values.priority}. Your form remains separate.
              </p>
              <p className="source-page-text">
                Saved notes: {observed.record.values.notes || 'None'}
              </p>
              <p className="source-page-text">
                Saved next action: {observed.record.values.next_action || 'None'}
              </p>
              <p>
                Saved next-action due date:{' '}
                {observed.record.values.next_action_due_date ?? 'Not recorded'}
              </p>
              <button
                disabled={busy}
                onClick={() => {
                  if (!discardAllowed()) return;
                  load(observed);
                  setError('');
                  setNotice('Loaded the current saved entry. Edit and save explicitly.');
                }}
              >
                Load current saved entry (discard my edits)
              </button>
            </aside>
          )}
        </section>
      )}
    </>
  );
}
