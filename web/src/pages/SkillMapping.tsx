import { useRef, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { Async } from '../components/ui';
import { MappingEditor } from '../components/MappingEditor';
import {
  mappingAnalyses,
  mappingSnapshots,
  mappingOverview,
  createMappingDraft,
  approveMapping,
  MappingInputError,
  MappingRequestError,
} from '../api/capabilityMappings';
import type { MappingRecord, MappingScope } from '../api/capabilityMappingTypes';

export function SkillMapping() {
  const [params] = useSearchParams();
  const [analysisId, setAnalysisId] = useState(() => params.get('analysis_id') ?? '');
  const [snapshotHash, setSnapshotHash] = useState('');
  const [scope, setScope] = useState<MappingScope | null>(null);
  const [findingId, setFindingId] = useState('');
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState('');
  const [failure, setFailure] = useState('');
  const running = useRef(false);
  const readVersion = useRef(0);
  const analyses = useQuery({
    queryKey: ['tendercite', 'mapping-analyses'],
    queryFn: ({ signal }) => mappingAnalyses(signal),
    retry: false,
    gcTime: 0,
  });
  const snapshots = useQuery({
    queryKey: ['tendercite', 'mapping-snapshots'],
    queryFn: ({ signal }) => mappingSnapshots(signal),
    retry: false,
    gcTime: 0,
  });
  const query = useQuery({
    queryKey: ['tendercite', 'mapping-overview', scope?.analysis_id, scope?.snapshot_hash],
    enabled: scope !== null,
    queryFn: async ({ signal }) => ({
      overview: await mappingOverview(scope!, signal),
      readVersion: ++readVersion.current,
    }),
    retry: false,
    gcTime: 0,
    staleTime: 0,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
  });
  function edit(change: () => void) {
    if (running.current) return;
    change();
    setScope(null);
    setFindingId('');
    setNotice('');
    setFailure('');
  }
  async function reload() {
    if (running.current || !scope) return;
    running.current = true;
    setBusy(true);
    setFailure('');
    setNotice('');
    try {
      await query.refetch({ throwOnError: true });
    } catch {
      setFailure(
        'Could not reload current overview. Mapping actions are disabled until a successful reload.',
      );
    } finally {
      running.current = false;
      setBusy(false);
    }
  }
  async function mutate(operation: () => Promise<MappingRecord>) {
    if (running.current || query.isFetching || query.isError || !query.data) return;
    running.current = true;
    setBusy(true);
    setNotice('');
    setFailure('');
    try {
      const record = await operation();
      setNotice(
        `Server confirmed ${record.status}: ${record.mapping_id}, revision ${record.revision}. Inspect the current state below. Approval is never automatic.`,
      );
    } catch (error) {
      setFailure(
        error instanceof MappingInputError
          ? error.message
          : `${error instanceof MappingRequestError ? error.message : 'Mapping response unavailable.'} Save outcome is uncertain: a failed response does not prove nothing was saved. Inspect refreshed state before an explicit retry. No automatic retry was made.`,
      );
    } finally {
      try {
        await query.refetch({ throwOnError: true });
      } catch {
        setFailure((previous) =>
          `${previous} Current overview could not be refreshed. Actions are disabled until a successful reload.`.trim(),
        );
      }
      running.current = false;
      setBusy(false);
    }
  }
  const data = query.data;
  const overview = data?.overview;
  const selected = overview?.findings.find((f) => f.finding_id === findingId);
  const disabled = busy || query.isFetching || query.isError;
  const selectionValid =
    analyses.data?.includes(analysisId) &&
    snapshots.data?.some((s) => s.content_hash === snapshotHash);
  return (
    <>
      <section className="panel">
        <h2>Skill Mapping</h2>
        <p>
          Manually associate a reviewed requirement with catalog skills from one explicitly selected
          snapshot. No skills, matching results or qualifications are inferred.
        </p>
        <p>
          The local mapping API is disabled by default. Enable
          TENDERCITE_CAPABILITY_MAPPING_API_ENABLED only for permitted synthetic development records
          and restart the backend.
        </p>
        <p>
          Analyses are discovered from current findings; there is no analysis-list endpoint.
          Snapshot selection lists at most 50 imported metadata records. Import another snapshot
          through <Link to="/tenders/workforce-snapshot/import">Import Workforce Snapshot</Link>.
        </p>
        <Async query={analyses}>
          {(ids) => (
            <label>
              Analysis
              <select
                value={analysisId}
                disabled={busy}
                onChange={(e) => edit(() => setAnalysisId(e.target.value))}
              >
                <option value="">Select an analysis with findings</option>
                {ids.map((id) => (
                  <option key={id} value={id}>
                    {id}
                  </option>
                ))}
              </select>
            </label>
          )}
        </Async>
        {analyses.isSuccess && !analyses.data.length && (
          <p>No analyses with findings are available. Create or review requirements first.</p>
        )}
        <Async query={snapshots}>
          {(rows) => (
            <label>
              Imported snapshot
              <select
                value={snapshotHash}
                disabled={busy}
                onChange={(e) => edit(() => setSnapshotHash(e.target.value))}
              >
                <option value="">Select one exact snapshot</option>
                {rows.map((s) => (
                  <option key={s.content_hash} value={s.content_hash}>
                    {s.assessment_period_start}–{s.assessment_period_end} · {s.content_hash}
                  </option>
                ))}
              </select>
            </label>
          )}
        </Async>
        {snapshots.isSuccess && !snapshots.data.length && <p>No imported snapshots available.</p>}
        <button
          type="button"
          disabled={!selectionValid || busy || query.isFetching}
          onClick={() => {
            if (running.current || !selectionValid) return;
            setFindingId('');
            setNotice('');
            setFailure('');
            if (scope?.analysis_id === analysisId && scope.snapshot_hash === snapshotHash)
              void reload();
            else setScope({ analysis_id: analysisId, snapshot_hash: snapshotHash });
          }}
        >
          Load mapping overview
        </button>
        {scope && (
          <button type="button" disabled={busy || query.isFetching} onClick={() => void reload()}>
            Reload current overview
          </button>
        )}
        <p>
          Reloading clears unsaved proposals and approval acknowledgement. A review or revision can
          change in another window; backend checks remain authoritative.
        </p>
      </section>
      {busy && <p role="status">Saving or refreshing current mapping state…</p>}
      {notice && <p role="status">{notice}</p>}
      {failure && <p role="alert">{failure}</p>}
      {scope && !data && <Async query={query}>{() => null}</Async>}
      {scope && overview && (
        <>
          {query.isError && (
            <p role="alert">
              Cached overview may be stale. Actions are disabled; reload current overview.
            </p>
          )}
          {query.isFetching && <p role="status">Loading authoritative overview…</p>}
          <section className="panel" aria-label="Mapping overview">
            <h3>Selected analysis and snapshot</h3>
            <Link to={`/tenders/analyses/${encodeURIComponent(overview.analysis_id)}`}>
              Analysis {overview.analysis_id}
            </Link>
            <p>Snapshot: {overview.snapshot_hash}</p>
            <p>
              Assessment period: {overview.snapshot.assessment_period.start_date} through{' '}
              {overview.snapshot.assessment_period.end_date} · Members:{' '}
              {overview.snapshot.workforce_member_count}
            </p>
            <details>
              <summary>Available skill catalog ({overview.snapshot.skill_catalog.length})</summary>
              <ul>
                {overview.snapshot.skill_catalog.map((s) => (
                  <li key={s.skill_id}>
                    {s.name} · {s.category} · {s.skill_id}
                  </li>
                ))}
              </ul>
            </details>
            <p>
              Review/source eligibility is not skill relevance. Deadlines, financial conditions,
              insurance or certificates can require other evidence; they are not converted into
              skills.
            </p>
            <ul>
              {overview.findings.map((f) => (
                <li key={f.finding_id}>
                  {f.effective_requirement.statement} · Review: {f.review_status} · Grounding:{' '}
                  {f.grounding_status} · Mapping: {f.mapping.status}
                </li>
              ))}
            </ul>
            {!overview.findings.length && <p>No findings in this analysis.</p>}
            <label>
              Requirement
              <select
                value={findingId}
                disabled={disabled}
                onChange={(e) => {
                  if (!running.current) setFindingId(e.target.value);
                }}
              >
                <option value="">Select a requirement to inspect or map</option>
                {overview.findings.map((f) => (
                  <option key={f.finding_id} value={f.finding_id}>
                    {f.effective_requirement.statement} · {f.mapping.status} · {f.finding_id}
                  </option>
                ))}
              </select>
            </label>
          </section>
          {selected && (
            <>
              <section className="panel" aria-label="Requirement source evidence">
                <Link to={`/tenders/findings/${encodeURIComponent(selected.finding_id)}`}>
                  Finding detail and human review
                </Link>
                <p>Original requirement: {selected.original_requirement.statement}</p>
                {selected.source_references.map((e, index) => (
                  <blockquote key={index}>
                    <p>{e.quote}</p>
                    <p>
                      {e.grounding_status} ·{' '}
                      <Link
                        to={`/tenders/documents/${encodeURIComponent(e.document_id)}/pages/${e.page_number}`}
                      >
                        View source PDF page {e.page_number}
                      </Link>
                    </p>
                  </blockquote>
                ))}
                <p>
                  Quotation verification establishes source occurrence, not interpretation
                  correctness or skill relevance.
                </p>
              </section>
              <MappingEditor
                key={`${selected.finding_id}:${data!.readVersion}`}
                overview={overview}
                finding={selected}
                disabled={disabled}
                create={(proposal) => mutate(() => createMappingDraft(proposal))}
                approve={(rationale, alias) =>
                  mutate(() => approveMapping(overview, selected, rationale, alias))
                }
              />
            </>
          )}
        </>
      )}
      <p>
        Mapping approval is an operator declaration. It does not verify experience, certification,
        professional qualification, bidder eligibility, legal compliance or capacity. Mapping
        revision/revocation and automated matching reports are outside this page.
      </p>
    </>
  );
}
