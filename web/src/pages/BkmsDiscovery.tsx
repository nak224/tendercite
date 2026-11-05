import { useEffect, useRef, useState } from 'react';
import { Link, useBlocker } from 'react-router-dom';
import { BkmsError, loadBkmsDay, importBkms, type BkmsCatalog } from '../api/bkms';
import {
  getBkmsCollection,
  removeBkmsDay,
  searchAllBkms,
  type BkmsCollection,
  type MultiBkmsResults,
  type NoticeFilter,
} from '../api/bkmsCatalogs';
import type { OpportunityRecord } from '../api/opportunities';
import { Badge, Empty } from '../components/ui';

type Outcome = {
  status?: 'CREATED' | 'ALREADY_PRESENT';
  record?: OpportunityRecord;
  error?: string;
};
type Confirmation = { kind: 'REPLACE' | 'REMOVE'; catalog: BkmsCatalog };
const categoryLabels: Record<NoticeFilter, string> = {
  ALL: 'All notice types',
  TENDER: 'Tender-related',
  AWARD: 'Award-related',
  PLANNING: 'Planning-related',
  OTHER_UNKNOWN: 'Other / unknown',
};
const message = (error: unknown) =>
  error instanceof BkmsError ? error.message : 'Request unavailable. No automatic retry was made.';

export function BkmsDiscovery({ onBusyChange }: { onBusyChange: (busy: boolean) => void }) {
  const [coverage, setCoverage] = useState<BkmsCollection | null>(null);
  const [day, setDay] = useState('');
  const [keyword, setKeyword] = useState('');
  const [limit, setLimit] = useState(10);
  const [category, setCategory] = useState<NoticeFilter>('ALL');
  const [earlier, setEarlier] = useState(false);
  const [results, setResults] = useState<MultiBkmsResults | null>(null);
  const [confirmation, setConfirmation] = useState<Confirmation | null>(null);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [invalidated, setInvalidated] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [outcomes, setOutcomes] = useState<Record<string, Outcome>>({});
  const pending = useRef<string | null>(null),
    catalogGeneration = useRef(0),
    alive = useRef(true);
  const blocker = useBlocker(() => pending.current !== null);
  async function refresh(signal?: AbortSignal) {
    const generation = ++catalogGeneration.current;
    try {
      const current = await getBkmsCollection(signal);
      if (alive.current && !signal?.aborted && generation === catalogGeneration.current) {
        setCoverage(current);
        setInvalidated(false);
        setConfirmation(null);
        setResults((old) => (old?.coverage.collection_id === current.collection_id ? old : null));
      }
    } catch (failure) {
      if (!signal?.aborted && generation === catalogGeneration.current) throw failure;
    }
  }
  useEffect(() => {
    alive.current = true;
    const controller = new AbortController();
    void refresh(controller.signal).catch((failure) => {
      if (!controller.signal.aborted && alive.current) setError(message(failure));
    });
    function unload(event: BeforeUnloadEvent) {
      if (pending.current) {
        event.preventDefault();
        event.returnValue = '';
      }
    }
    window.addEventListener('beforeunload', unload);
    return () => {
      alive.current = false;
      controller.abort();
      window.removeEventListener('beforeunload', unload);
    };
  }, []);
  async function perform(operation: string, action: () => Promise<void>) {
    if (pending.current) return;
    pending.current = operation;
    setBusy(operation);
    onBusyChange(true);
    try {
      await action();
    } catch (failure) {
      if (alive.current) setError(message(failure));
    } finally {
      pending.current = null;
      if (alive.current) {
        setBusy(null);
        onBusyChange(false);
      }
    }
  }
  function invalidate() {
    catalogGeneration.current += 1;
    setInvalidated(true);
    setResults(null);
    setError('');
    setNotice('');
    setConfirmation(null);
  }
  async function recoverList() {
    try {
      await refresh();
    } catch {
      if (alive.current)
        setError(
          (old) =>
            `${old} Current stored-day metadata could not be read; use the explicit read action.`,
        );
    }
  }
  async function load(selectedDay = day, expected?: string) {
    await perform('LOAD', async () => {
      invalidate();
      try {
        await loadBkmsDay(selectedDay, expected);
        if (alive.current)
          setNotice(
            `Server confirmed ${expected ? 'replacement of' : 'loading'} ${selectedDay}. Saved opportunities were not changed.`,
          );
      } catch (failure) {
        if (alive.current)
          setError(
            `${message(failure)} Download/storage outcome is not confirmed. Previously stored days may still be present. No automatic retry was made.`,
          );
      }
      await recoverList();
    });
  }
  async function remove(catalog: BkmsCatalog) {
    await perform('REMOVE', async () => {
      invalidate();
      try {
        const current = await removeBkmsDay(catalog.export_day, catalog.catalog_id);
        if (alive.current) {
          setCoverage(current);
          setInvalidated(false);
          setNotice(
            `Server confirmed removal of ${catalog.export_day} from discovery. Imported opportunities remain unchanged.`,
          );
        }
      } catch (failure) {
        if (alive.current)
          setError(
            `${message(failure)} Removal is not confirmed; read current stored days before explicitly retrying. No automatic retry was made.`,
          );
        await recoverList();
      }
    });
  }
  async function search(
    page = 1,
    phrase = keyword,
    size = limit,
    filter = category,
    versions = earlier,
  ) {
    if (!coverage || invalidated) return;
    setError('');
    await perform('SEARCH', async () => {
      try {
        const current = await searchAllBkms(
          coverage.collection_id,
          phrase,
          page,
          size,
          filter,
          versions,
        );
        if (alive.current) setResults(current);
      } catch (failure) {
        if (alive.current) {
          setResults(null);
          if (failure instanceof BkmsError && failure.code === 'BKMS_CATALOG_CONFLICT')
            setInvalidated(true);
        }
        throw failure;
      }
    });
  }
  async function save(id: string, catalogId: string) {
    if (outcomes[id]?.record || invalidated) return;
    await perform(id, async () => {
      try {
        const outcome = await importBkms(catalogId, id);
        if (alive.current) setOutcomes((old) => ({ ...old, [id]: outcome }));
      } catch (failure) {
        if (alive.current) {
          if (
            failure instanceof BkmsError &&
            ['BKMS_CATALOG_CONFLICT', 'BKMS_CATALOG_NOT_FOUND'].includes(failure.code)
          )
            setInvalidated(true);
          setOutcomes((old) => ({
            ...old,
            [id]: {
              error: `${message(failure)} Save is not confirmed; an opportunity may already exist. Check Opportunities before an explicit retry. No automatic retry was made.`,
            },
          }));
        }
      }
    });
  }
  const selected = coverage?.days.find((d) => d.catalog.export_day === day)?.catalog;
  const full = (coverage?.days.length ?? 0) >= 7;
  return (
    <>
      {blocker.state === 'blocked' && (
        <aside
          role="alertdialog"
          aria-label="German procurement operation in progress"
          className="panel"
        >
          <p>
            Wait for the current response. Leaving can hide an uncertain catalog or opportunity
            save.
          </p>
          <button autoFocus onClick={() => blocker.reset()}>
            Stay on Discover
          </button>
          <button disabled={busy !== null} onClick={() => blocker.proceed()}>
            Continue navigation
          </button>
        </aside>
      )}
      <section className="panel" aria-label="German procurement catalog">
        <h2>German Bekanntmachungsservice — Open Data</h2>
        <p>
          Enable TENDERCITE_BKMS_DISCOVERY_API_ENABLED separately. CC Zero covers the official Open
          Data; personal-data and third-party rights still apply. The public Open Data API provides
          daily exports, not keyword search. Downloads are explicit; searches use only stored
          metadata.
        </p>
        <p>
          Retain up to seven publication days and 96 MiB of active catalog data. No automatic
          expiry, background download, monthly archive or attachment fetch. Saved opportunities are
          independent of catalog changes.
        </p>
        <form
          onSubmit={(e) => {
            e.preventDefault();
            if (!selected && !full) void load();
          }}
        >
          <fieldset disabled={busy !== null}>
            <legend>Explicit daily download (maximum 16 MiB compressed / 4,000 notices)</legend>
            <label>
              Publication day (Europe/Berlin)
              <input
                type="date"
                min="2022-12-01"
                required
                value={day}
                onChange={(e) => {
                  setDay(e.target.value);
                  setConfirmation(null);
                }}
              />
            </label>
            <button className="primary" type="submit" disabled={!!selected || full}>
              Load publication day
            </button>
            {selected && (
              <button
                type="button"
                disabled={invalidated}
                onClick={() => setConfirmation({ kind: 'REPLACE', catalog: selected })}
              >
                Replace stored day
              </button>
            )}
          </fieldset>
        </form>
        {selected && (
          <p>
            This day is already stored. Only an explicit confirmed replacement downloads new
            metadata.
          </p>
        )}
        {full && (
          <p>
            Seven days are retained. Remove an unwanted day explicitly before adding another; no day
            is deleted automatically.
          </p>
        )}
        <button
          disabled={busy !== null}
          onClick={() => {
            setError('');
            void perform('REFRESH', () => refresh());
          }}
        >
          Read current local catalog
        </button>
        {busy === 'LOAD' && (
          <p role="status">Downloading and validating one official daily export…</p>
        )}
        {busy === 'REMOVE' && <p role="status">Removing the selected stored day…</p>}
        {confirmation && (
          <aside role="alertdialog" aria-label="Confirm catalog change">
            <p>
              {confirmation.kind === 'REPLACE' ? 'Replace' : 'Remove'}{' '}
              {confirmation.catalog.export_day} in local discovery? Imported opportunities and
              original source provenance remain unchanged. The migrated legacy recovery copy, if
              any, is retained.
            </p>
            <button
              disabled={busy !== null}
              onClick={() => {
                const c = confirmation;
                if (c.kind === 'REPLACE') void load(c.catalog.export_day, c.catalog.catalog_id);
                else void remove(c.catalog);
              }}
            >
              {confirmation.kind === 'REPLACE' ? 'Confirm day replacement' : 'Confirm day removal'}
            </button>
            <button disabled={busy !== null} onClick={() => setConfirmation(null)}>
              Cancel catalog change
            </button>
          </aside>
        )}
        {coverage ? (
          <aside aria-label="Loaded German catalog" className="source-page-text">
            <h3>
              Stored publication days ({coverage.days.length} / {coverage.max_days})
            </h3>
            <p>
              Local coverage:{' '}
              {coverage.days.map((d) => d.catalog.export_day).join(', ') || 'No stored days'}. Gaps
              and all unretained dates are outside this search.
            </p>
            <p>
              Active storage: {coverage.stored_bytes.toLocaleString()} /{' '}
              {coverage.max_storage_bytes.toLocaleString()} bytes.
            </p>
            {!coverage.days.length && (
              <Empty>
                No daily catalog loaded. Select a completed publication day; no provider request
                runs automatically.
              </Empty>
            )}
            <ul aria-label="Stored publication days">
              {coverage.days.map(({ catalog, warning_counts }) => (
                <li className="panel" key={catalog.export_day}>
                  <strong>{catalog.export_day}</strong> · {catalog.record_count} notices ·{' '}
                  {catalog.importable_count} with titles · {catalog.warning_count} with mapping
                  warnings.
                  <p>Catalog retrieved: {catalog.retrieved_at}</p>
                  <details>
                    <summary>Stored day provenance and warnings</summary>
                    <p>Catalog ID: {catalog.catalog_id}</p>
                    <p>Archive SHA-256: {catalog.archive_sha256}</p>
                    {Object.entries(warning_counts).length ? (
                      <ul>
                        {Object.entries(warning_counts).map(([code, n]) => (
                          <li key={code}>
                            {code}: {n} notices
                          </li>
                        ))}
                      </ul>
                    ) : (
                      <p>No mapping warnings recorded.</p>
                    )}
                  </details>
                  <button
                    disabled={busy !== null || invalidated}
                    onClick={() => setConfirmation({ kind: 'REMOVE', catalog })}
                  >
                    Remove {catalog.export_day}
                  </button>
                </li>
              ))}
            </ul>
          </aside>
        ) : (
          <p role="status">
            {error ? 'Stored-day metadata unavailable.' : 'Reading stored-day metadata…'}
          </p>
        )}
        {invalidated && (
          <p>
            Current local coverage is unconfirmed. Read current stored-day metadata before searching
            or saving.
          </p>
        )}
        {notice && <p role="status">{notice}</p>}
        {error && <p role="alert">{error}</p>}
      </section>
      <section className="panel" aria-label="German procurement local search">
        <form
          onSubmit={(e) => {
            e.preventDefault();
            void search();
          }}
        >
          <fieldset disabled={!coverage?.days.length || invalidated || busy !== null}>
            <legend>Search across retained publication days</legend>
            <div className="form-grid">
              <label>
                Keyword phrase
                <input
                  required
                  maxLength={150}
                  value={keyword}
                  onChange={(e) => setKeyword(e.target.value)}
                />
              </label>
              <label>
                Notice type
                <select
                  value={category}
                  onChange={(e) => setCategory(e.target.value as NoticeFilter)}
                >
                  {Object.entries(categoryLabels).map(([code, label]) => (
                    <option key={code} value={code}>
                      {label}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                Results per page
                <select value={limit} onChange={(e) => setLimit(Number(e.target.value))}>
                  {[5, 10, 20].map((n) => (
                    <option key={n} value={n}>
                      {n}
                    </option>
                  ))}
                </select>
              </label>
            </div>
            <label>
              <input
                type="checkbox"
                checked={earlier}
                onChange={(e) => setEarlier(e.target.checked)}
              />
              Show all stored notice versions
            </label>
            <button className="primary" type="submit">
              Search local notices
            </button>
          </fieldset>
        </form>
        <p>
          Literal case-insensitive phrase in retained title, buyer or description (up to 8,000
          characters). Default: highest numeric version among stored versions, before keyword/type
          filtering; equal numeric labels remain distinct. Results use source publication
          timestamps, newest first, with stable ties. Missing timestamps are last.
        </p>
        <p>
          Tags include updates and cancellations; tender-related never means open for bidding. Other
          / unknown includes missing, unfamiliar, mixed and other OCDS tags. Missing deadlines
          remain unavailable; publication dates and catalog retrieval dates are separate.
        </p>
        {busy === 'SEARCH' && <p role="status">Searching local metadata…</p>}
      </section>
      {results && (
        <section className="panel" aria-label="German procurement search results">
          <h2>
            Results for “{results.keyword}” · page {results.page}
          </h2>
          <p>
            {results.total_count} matches in retained publication days only:{' '}
            {results.coverage.days.map((d) => d.catalog.export_day).join(', ')}. Type:{' '}
            {categoryLabels[results.notice_category]}.{' '}
            {results.include_earlier_versions
              ? 'All stored versions.'
              : 'Highest stored numeric versions only.'}
          </p>
          {!results.items.length && (
            <Empty>
              No local notices match this keyword and view. This does not mean no matching public
              procurements exist elsewhere; earlier versions may also contain different text.
            </Empty>
          )}
          <ul className="discovery-results">
            {results.items.map((hit) => {
              const item = hit.source,
                m = item.original_metadata,
                outcome = outcomes[item.external_id];
              return (
                <li className="panel" key={item.external_id}>
                  <h3>{m.title?.trim() || 'Title not supplied'}</h3>
                  <Badge>Bekanntmachungsservice · {item.external_id}</Badge>
                  <p>Organization: {m.buyer?.trim() || 'Not supplied'}</p>
                  <p>
                    Notice type: {categoryLabels[hit.notice_category]}. Source tags:{' '}
                    {m.tags.join(', ') || 'Unknown / not supplied'}
                  </p>
                  <p>Publication date (source timestamp): {m.published_at ?? 'Unavailable'}</p>
                  <p>
                    Deadline (source timestamp):{' '}
                    {m.deadline ?? 'Unavailable in OCDS; inspect the original publication'}
                  </p>
                  <p>
                    {hit.is_latest_stored
                      ? 'Highest stored numeric version.'
                      : 'Earlier stored numeric version.'}{' '}
                    {hit.version_count} distinct stored version label(s).
                  </p>
                  <p className="source-page-text">
                    {m.description
                      ? `${m.description.slice(0, 300)}${m.description.length > 300 ? '…' : ''}`
                      : 'Description not supplied'}
                  </p>
                  <details>
                    <summary>Inspect German notice</summary>
                    <p>
                      Shown snapshot: export {item.export_day}, retrieved {item.retrieved_at}. OCID:{' '}
                      {item.ocid ?? 'Not supplied'}. No snapshot fields were merged.
                    </p>
                    <p className="source-page-text">{m.description ?? 'No description supplied'}</p>
                    <p>
                      License: CC Zero. Source metadata does not verify legal interpretation or
                      bidder eligibility. No match with TED is inferred.
                    </p>
                    <ul>
                      {item.mapping_warnings.map((w) => (
                        <li key={w}>{w}</li>
                      ))}
                    </ul>
                    <p>
                      Observed in {hit.occurrences.length} retained daily export(s).{' '}
                      {hit.source_record_hashes_differ
                        ? 'The source record hashes differ across days.'
                        : 'Source record hashes agree.'}
                    </p>
                    <ul>
                      {hit.occurrences.map((o) => (
                        <li key={o.export_day}>
                          Export {o.export_day} · catalog {o.catalog_id} · record SHA-256{' '}
                          {o.record_sha256}
                        </li>
                      ))}
                    </ul>
                  </details>
                  <p>
                    <a
                      href={item.original_url}
                      target="_blank"
                      rel="noopener noreferrer"
                      referrerPolicy="no-referrer"
                    >
                      Original German publication
                    </a>
                  </p>
                  {!outcome?.record && (
                    <button
                      disabled={!m.title?.trim() || busy !== null || invalidated}
                      onClick={() => void save(item.external_id, hit.occurrences[0]!.catalog_id)}
                    >
                      Save to Opportunities
                    </button>
                  )}
                  {busy === item.external_id && <p role="status">Saving selected German notice…</p>}
                  {outcome?.error && (
                    <p role="alert">
                      {outcome.error} <Link to="/opportunities">View Opportunities</Link>
                    </p>
                  )}
                  {outcome?.record && (
                    <aside aria-label={`Saved German publication ${item.external_id}`}>
                      <p role="status">
                        Server confirmed {outcome.status}.{' '}
                        {outcome.status === 'ALREADY_PRESENT'
                          ? 'Existing local edits were retained.'
                          : 'Saved with official German attribution and no inferred skills.'}
                      </p>
                      <div className="actions">
                        <Link to={`/opportunities?opportunity_id=${outcome.record.opportunity_id}`}>
                          View Opportunity
                        </Link>
                        <Link to={`/matching?opportunity_id=${outcome.record.opportunity_id}`}>
                          Compare Skills
                        </Link>
                        <Link to={`/pipeline?opportunity_id=${outcome.record.opportunity_id}`}>
                          Add to Pipeline
                        </Link>
                      </div>
                    </aside>
                  )}
                </li>
              );
            })}
          </ul>
          <div className="actions">
            <button
              disabled={busy !== null || invalidated || results.page === 1}
              onClick={() =>
                void search(
                  results.page - 1,
                  results.keyword,
                  results.limit,
                  results.notice_category,
                  results.include_earlier_versions,
                )
              }
            >
              Previous results
            </button>
            <button
              disabled={busy !== null || invalidated || results.next_page === null}
              onClick={() => {
                if (results.next_page !== null)
                  void search(
                    results.next_page,
                    results.keyword,
                    results.limit,
                    results.notice_category,
                    results.include_earlier_versions,
                  );
              }}
            >
              Next results
            </button>
          </div>
        </section>
      )}
    </>
  );
}
