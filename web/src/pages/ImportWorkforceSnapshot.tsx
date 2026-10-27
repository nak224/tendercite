import { useRef, useState } from 'react';
import {
  readSnapshotFile,
  sendSnapshot,
  SnapshotImportError,
  type SnapshotSummary,
} from '../api/tenderciteSnapshots';

export function ImportWorkforceSnapshot() {
  const [bytes, setBytes] = useState<ArrayBuffer | null>(null);
  const [reading, setReading] = useState(false);
  const [busy, setBusy] = useState<'validate' | 'import' | null>(null);
  const [validated, setValidated] = useState<SnapshotSummary | null>(null);
  const [imported, setImported] = useState<SnapshotSummary | null>(null);
  const [acknowledged, setAcknowledged] = useState(false);
  const [error, setError] = useState('');
  const [uncertain, setUncertain] = useState(false);
  const running = useRef(false);
  const revision = useRef(0);

  async function select(file?: File) {
    const version = ++revision.current;
    setBytes(null);
    setValidated(null);
    setImported(null);
    setAcknowledged(false);
    setError('');
    setUncertain(false);
    setReading(!!file);
    if (!file) return;
    try {
      const original = await readSnapshotFile(file);
      if (version === revision.current) setBytes(original);
    } catch (failure) {
      if (version === revision.current)
        setError(
          failure instanceof SnapshotImportError ? failure.message : 'Cannot read the JSON file.',
        );
    } finally {
      if (version === revision.current) setReading(false);
    }
  }
  async function act(operation: 'validate' | 'import') {
    if (
      running.current ||
      reading ||
      !bytes ||
      (operation === 'import' && (!validated || !acknowledged))
    )
      return;
    running.current = true;
    const version = revision.current;
    const previous = validated;
    const confirmed = acknowledged;
    setBusy(operation);
    setValidated(null);
    setImported(null);
    setAcknowledged(false);
    setError('');
    setUncertain(false);
    try {
      const summary = await sendSnapshot(operation, bytes, previous ?? undefined, confirmed);
      if (version !== revision.current) {
        if (operation === 'import') {
          setUncertain(true);
          setError('An earlier file was submitted. Check its import before continuing.');
        }
        return;
      }
      if (operation === 'validate') setValidated(summary);
      else setImported(summary);
    } catch (failure) {
      if (version === revision.current || operation === 'import') {
        setError(
          failure instanceof SnapshotImportError ? failure.message : 'Snapshot request failed.',
        );
        setUncertain(operation === 'import');
      }
    } finally {
      running.current = false;
      setBusy(null);
    }
  }
  const summary = imported ?? validated;
  return (
    <section className="panel">
      <h2>Import Workforce Snapshot</h2>
      <p>
        Obtain a snapshot from Team Capacity Engine or another compatible schema 1.0 producer. That
        application is optional; TenderCite validates and imports the file but does not generate
        workforce snapshots or connect to a workforce server.
      </p>
      <p>
        Select a permitted local synthetic schema 1.0 JSON file. Validation checks the complete
        contract on TenderCite without importing it. Export and import are separate actions; nothing
        is transferred automatically.
      </p>
      <p>
        The TenderCite snapshot API is disabled by default. Enable{' '}
        TENDERCITE_CAPABILITY_SNAPSHOT_API_ENABLED on the local synthetic backend and restart it.
      </p>
      <label>
        Capability snapshot JSON
        <input
          type="file"
          accept=".json,application/json"
          disabled={!!busy || reading}
          onChange={(event) => void select(event.target.files?.[0])}
        />
      </label>
      <p>Maximum 16 MiB. Original file bytes are retained unchanged in page memory only.</p>
      {bytes && <p>Selected JSON: {bytes.byteLength} bytes.</p>}
      {reading && <p role="status">Reading selected JSON…</p>}
      <button
        type="button"
        disabled={!bytes || !!busy || reading}
        onClick={() => void act('validate')}
      >
        Validate Snapshot
      </button>
      {busy && (
        <p role="status">{busy === 'validate' ? 'Validating snapshot…' : 'Importing snapshot…'}</p>
      )}
      {error && (
        <div role="alert">
          <p>{error}</p>
          {uncertain && (
            <p>
              Import outcome uncertain: the snapshot may already be stored. No automatic retry was
              made. Your file remains selected. Explicitly validate it again before another import
              attempt; an identical stored snapshot returns ALREADY_PRESENT.
            </p>
          )}
        </div>
      )}
      {summary && (
        <section aria-label="Snapshot metadata">
          <h3>{imported ? 'Import confirmed' : 'Validation passed — not imported'}</h3>
          <p>Status: {summary.status}</p>
          <p>
            Schema: {summary.schema_version} · Source: {summary.source_system}
          </p>
          <p>Content hash: {summary.content_hash}</p>
          <p>
            Assessment period: {summary.assessment_period.start_date} through{' '}
            {summary.assessment_period.end_date} (inclusive)
          </p>
          <p>
            Members: {summary.member_count} · Assignments: {summary.assignment_count} · Catalog
            skills: {summary.catalog_count}
          </p>
          {summary.imported_at && <p>Original import time: {summary.imported_at}</p>}
          {imported && (
            <p>
              {imported.status === 'ALREADY_PRESENT'
                ? 'The identical snapshot was already stored; its original metadata is preserved.'
                : 'TenderCite confirmed the snapshot was stored.'}{' '}
              To repeat explicitly, validate this selected file again and acknowledge it anew.
            </p>
          )}
        </section>
      )}
      {validated && (
        <div>
          <label>
            <input
              type="checkbox"
              checked={acknowledged}
              disabled={!!busy}
              onChange={(event) => setAcknowledged(event.target.checked)}
            />
            I acknowledge this file contains only permitted local synthetic development records.
            Internal skill assignments do not prove professional qualification. Capacity is not
            guaranteed contractual availability.
          </label>
          <button
            type="button"
            disabled={!acknowledged || !!busy || reading}
            onClick={() => void act('import')}
          >
            Import Snapshot
          </button>
        </div>
      )}
      <p>
        Acknowledgement is an operator declaration, not independent provenance verification.
        Imported assignments do not establish bidder eligibility or legal compliance. Snapshot
        history/export, mapping revision/revocation and capability matching remain CLI-only.
        Explicit manual mapping drafts and approvals are available in the Skill Mapping view.
      </p>
    </section>
  );
}
