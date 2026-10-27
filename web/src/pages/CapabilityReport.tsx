import { useEffect, useRef, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { Async } from '../components/ui';
import { CapabilityReportResults } from '../components/CapabilityReportResults';
import { mappingAnalyses, mappingSnapshots } from '../api/capabilityMappings';
import { hash } from '../api/capabilityMappingValidation';
import {
  assessmentSelection,
  capabilityReport,
  reportFindings,
  ReportInputError,
  ReportRequestError,
} from '../api/capabilityReports';
import type { AssessmentReport } from '../api/capabilityReportTypes';

export function CapabilityReport() {
  const [params] = useSearchParams();
  const [analysisId, setAnalysisId] = useState(() => params.get('analysis_id') ?? '');
  const [snapshotHash, setSnapshotHash] = useState('');
  const [findingIds, setFindingIds] = useState<string[]>([]);
  // Private report data deliberately stays outside TanStack Query and browser storage.
  const [report, setReport] = useState<AssessmentReport | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const active = useRef<AbortController | null>(null);
  useEffect(
    () => () => {
      active.current?.abort();
      active.current = null;
    },
    [],
  );
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
  const findings = useQuery({
    queryKey: ['tendercite', 'report-findings', analysisId],
    enabled: !!analysisId,
    queryFn: ({ signal }) => reportFindings(analysisId, signal),
    retry: false,
    gcTime: 0,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
  });
  function clear() {
    active.current?.abort();
    active.current = null;
    setBusy(false);
    setReport(null);
    setError('');
  }
  const ready =
    analyses.data?.includes(analysisId) &&
    hash(snapshotHash) &&
    findings.isSuccess &&
    !findings.isFetching &&
    findingIds.every((id) => findings.data.some((f) => f.id === id)) &&
    findingIds.length <= 100;
  async function run() {
    if (active.current || !ready) return;
    const controller = new AbortController();
    active.current = controller;
    setReport(null);
    setError('');
    setBusy(true);
    try {
      const result = await capabilityReport(
        assessmentSelection(analysisId, snapshotHash, findingIds),
        controller.signal,
      );
      if (active.current === controller && !controller.signal.aborted) setReport(result);
    } catch (failure) {
      if (active.current === controller && !controller.signal.aborted)
        setError(
          failure instanceof ReportInputError || failure instanceof ReportRequestError
            ? failure.message
            : 'Capability report unavailable. No automatic retry was made.',
        );
    } finally {
      if (active.current === controller) {
        active.current = null;
        setBusy(false);
      }
    }
  }
  return (
    <>
      <section className="panel">
        <h2>Capability Matching</h2>
        <p>
          Inspect recorded individual skill coverage using explicit UUID membership and manually
          approved mappings. No skills or qualification decisions are inferred.
        </p>
        <p>
          Reports include private pseudonymous workforce records. They are held only on this page,
          not saved in browser storage. Missing assignments do not prove absent skills.
        </p>
        <p>
          Enable TENDERCITE_CAPABILITY_REPORT_API_ENABLED for the local backend. This separate flag
          is disabled by default and is not authentication.
        </p>
        <div className="form-grid">
          <Async query={analyses}>
            {(ids) => (
              <label>
                Analysis
                <select
                  value={analysisId}
                  onChange={(event) => {
                    clear();
                    setAnalysisId(event.target.value);
                    setFindingIds([]);
                  }}
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
          <label>
            Imported snapshot
            <select
              value={
                snapshots.data?.some((s) => s.content_hash === snapshotHash) ? snapshotHash : ''
              }
              onChange={(event) => {
                clear();
                setSnapshotHash(event.target.value);
              }}
            >
              <option value="">Select a snapshot or enter its exact hash below</option>
              {snapshots.data?.map((s) => (
                <option key={s.content_hash} value={s.content_hash}>
                  {s.assessment_period_start}–{s.assessment_period_end} · {s.content_hash}
                </option>
              ))}
            </select>
          </label>
          <label>
            Exact snapshot hash
            <input
              value={snapshotHash}
              maxLength={71}
              placeholder="sha256:…"
              onChange={(event) => {
                clear();
                setSnapshotHash(event.target.value);
              }}
            />
          </label>
        </div>
        <p>
          Snapshot discovery lists at most 50 records and requires
          TENDERCITE_CAPABILITY_MAPPING_API_ENABLED. The exact-hash fallback works with only the
          report flag; the backend validates snapshot existence. Analyses are discovered from
          current findings, not an analysis-list endpoint.
        </p>
        {snapshots.isPending && <p role="status">Loading snapshot metadata…</p>}
        {snapshots.isError && (
          <p role="status">
            Snapshot discovery unavailable. Enter the exact imported snapshot hash to continue
            without the mapping API.
          </p>
        )}
        {snapshots.isSuccess && !snapshots.data.length && (
          <p>No imported snapshots listed. An exact existing hash may be entered.</p>
        )}
        {analyses.isSuccess && !analyses.data.length && <p>No analyses with findings available.</p>}
        {analysisId && (
          <Async query={findings}>
            {(rows) => (
              <fieldset>
                <legend>Explicit requirement selection ({findingIds.length}/100)</legend>
                {!rows.length && <p>No findings in this analysis.</p>}
                {rows.map((finding) => (
                  <label key={finding.id}>
                    <input
                      type="checkbox"
                      checked={findingIds.includes(finding.id)}
                      disabled={findingIds.length >= 100 && !findingIds.includes(finding.id)}
                      onChange={(event) => {
                        clear();
                        setFindingIds((ids) =>
                          event.target.checked
                            ? [...ids, finding.id]
                            : ids.filter((id) => id !== finding.id),
                        );
                      }}
                    />
                    {finding.effective_value.statement} · Review: {finding.review_status} ·{' '}
                    {finding.id}
                  </label>
                ))}
              </fieldset>
            )}
          </Async>
        )}
        {!findingIds.length && (
          <p>
            No requirements selected. An explicit run returns an empty report; nothing is selected
            automatically.
          </p>
        )}
        <button type="button" disabled={!ready || busy} onClick={() => void run()}>
          Run Capability Matching
        </button>
        {busy && <p role="status">Computing capability report…</p>}
        {error && <p role="alert">{error}</p>}
      </section>
      <p>
        Recorded skill coverage is not verified qualification, experience, certification, capacity,
        bidder eligibility or legal compliance. There is no automatic Go/No-Go verdict.
      </p>
      {report && <CapabilityReportResults report={report} />}
    </>
  );
}
