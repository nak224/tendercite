import { useEffect, useRef, useState } from 'react';
import { Link, useBlocker } from 'react-router-dom';
import {
  BkmsError,
  currentBkmsCatalog,
  loadBkmsDay,
  searchBkms,
  importBkms,
  type BkmsCatalog,
  type BkmsResults,
} from '../api/bkms';
import type { OpportunityRecord } from '../api/opportunities';
import { Badge, Empty } from '../components/ui';

type Outcome = {
  status?: 'CREATED' | 'ALREADY_PRESENT';
  record?: OpportunityRecord;
  error?: string;
};
export function BkmsDiscovery({ onBusyChange }: { onBusyChange: (busy: boolean) => void }) {
  const [catalog, setCatalog] = useState<BkmsCatalog | null>(null);
  const [day, setDay] = useState('');
  const [keyword, setKeyword] = useState('');
  const [limit, setLimit] = useState(10);
  const [results, setResults] = useState<BkmsResults | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState<string | null>(null);
  const [outcomes, setOutcomes] = useState<Record<string, Outcome>>({});
  const pending = useRef<string | null>(null);
  const catalogGeneration = useRef(0);
  const alive = useRef(true);
  const blocker = useBlocker(() => pending.current !== null);
  async function refresh(signal?: AbortSignal) {
    const generation = ++catalogGeneration.current;
    try {
      const current = await currentBkmsCatalog(signal);
      if (alive.current && !signal?.aborted && generation === catalogGeneration.current) {
        setCatalog(current);
        setResults((old) => (old?.catalog.catalog_id === current?.catalog_id ? old : null));
      }
    } catch (failure) {
      if (!signal?.aborted && generation === catalogGeneration.current) throw failure;
    }
  }
  useEffect(() => {
    alive.current = true;
    const controller = new AbortController();
    void refresh(controller.signal).catch((failure) => {
      if (!controller.signal.aborted && alive.current)
        setError(failure instanceof BkmsError ? failure.message : 'Local catalog unavailable.');
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
      if (alive.current)
        setError(
          failure instanceof BkmsError
            ? failure.message
            : 'Request unavailable. No automatic retry was made.',
        );
    } finally {
      pending.current = null;
      if (alive.current) {
        setBusy(null);
        onBusyChange(false);
      }
    }
  }
  async function load() {
    setError('');
    await perform('LOAD', async () => {
      catalogGeneration.current += 1;
      try {
        const current = await loadBkmsDay(day);
        if (alive.current) {
          setCatalog(current);
          setResults(null);
          setError('');
        }
      } catch (failure) {
        if (alive.current)
          setError(
            `${failure instanceof BkmsError ? failure.message : 'Download response unavailable.'} Catalog replacement is unconfirmed. Read the current local catalog before explicitly retrying. No automatic retry was made.`,
          );
      }
    });
  }
  async function search(page = 1, phrase = keyword, size = limit) {
    if (!catalog) return;
    setError('');
    await perform('SEARCH', async () => {
      try {
        const current = await searchBkms(catalog.catalog_id, phrase, page, size);
        if (alive.current) setResults(current);
      } catch (failure) {
        if (alive.current) setResults(null);
        throw failure;
      }
    });
  }
  async function save(id: string, catalogId: string) {
    if (outcomes[id]?.record) return;
    await perform(id, async () => {
      try {
        const outcome = await importBkms(catalogId, id);
        if (alive.current) setOutcomes((old) => ({ ...old, [id]: outcome }));
      } catch (failure) {
        if (alive.current)
          setOutcomes((old) => ({
            ...old,
            [id]: {
              error: `${failure instanceof BkmsError ? failure.message : 'Import response unavailable.'} Save is not confirmed; an opportunity may already exist. Check Opportunities before an explicit retry. No automatic retry was made.`,
            },
          }));
      }
    });
  }
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
          Data; personal-data and third-party rights still apply. There is no public keyword API
          here: download one completed Berlin publication day, then search the bounded local
          catalog.
        </p>
        <p>
          One catalog is retained. Loading another day replaces that catalog, never saved
          opportunities. No monthly archive, automatic download, background update or document
          attachment is fetched.
        </p>
        <form
          onSubmit={(e) => {
            e.preventDefault();
            void load();
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
                onChange={(e) => setDay(e.target.value)}
              />
            </label>
            <button className="primary" type="submit">
              Load publication day
            </button>
          </fieldset>
        </form>
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
        {catalog ? (
          <aside aria-label="Loaded German catalog" className="source-page-text">
            <p>
              Loaded day: {catalog.export_day} · {catalog.record_count} notices ·{' '}
              {catalog.importable_count} with titles · {catalog.warning_count} with mapping
              warnings.
            </p>
            <p>
              Retrieved: {catalog.retrieved_at}. Catalog ID: {catalog.catalog_id}
            </p>
            <p>Archive SHA-256: {catalog.archive_sha256}</p>
          </aside>
        ) : (
          <Empty>
            No daily catalog loaded. Select a completed publication day; no provider request runs
            automatically.
          </Empty>
        )}
        {error && <p role="alert">{error}</p>}
      </section>
      <section className="panel" aria-label="German procurement local search">
        <form
          onSubmit={(e) => {
            e.preventDefault();
            void search();
          }}
        >
          <fieldset disabled={!catalog || busy !== null}>
            <legend>Search only the loaded daily catalog</legend>
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
            <button className="primary" type="submit">
              Search local notices
            </button>
          </fieldset>
        </form>
        <p>
          Literal case-insensitive phrase in retained title, buyer or description. This single-day
          subset includes award/planning notices and older versions; tags are source metadata, not
          proof that bidding is open. Descriptions retain at most 8,000 characters.
        </p>
        {busy === 'SEARCH' && <p role="status">Searching local metadata…</p>}
      </section>
      {results && (
        <section className="panel" aria-label="German procurement search results">
          <h2>
            Results for “{results.keyword}” · day {results.catalog.export_day} · page {results.page}
          </h2>
          <p>{results.total_count} matches in this loaded catalog only.</p>
          {!results.items.length && <Empty>No local notices match this keyword.</Empty>}
          <ul className="discovery-results">
            {results.items.map((item) => {
              const m = item.original_metadata,
                outcome = outcomes[item.external_id];
              return (
                <li className="panel" key={item.external_id}>
                  <h3>{m.title?.trim() || 'Title not supplied'}</h3>
                  <Badge>Bekanntmachungsservice · {item.external_id}</Badge>
                  <p>Organization: {m.buyer?.trim() || 'Not supplied'}</p>
                  <p>Source tags: {m.tags.join(', ') || 'Not supplied'}</p>
                  <p>Deadline (source timestamp): {m.deadline ?? 'Not supplied by OCDS'}</p>
                  <p className="source-page-text">
                    {m.description
                      ? `${m.description.slice(0, 300)}${m.description.length > 300 ? '…' : ''}`
                      : 'Description not supplied'}
                  </p>
                  <details>
                    <summary>Inspect German notice</summary>
                    <p>
                      Published (source timestamp): {m.published_at ?? 'Not supplied'}. OCID:{' '}
                      {item.ocid ?? 'Not supplied'}.
                    </p>
                    <p className="source-page-text">{m.description ?? 'No description supplied'}</p>
                    <p>
                      License: CC Zero · Retrieved: {item.retrieved_at}. This metadata does not
                      verify legal interpretation or bidder eligibility. No match with TED is
                      inferred.
                    </p>
                    <ul>
                      {item.mapping_warnings.map((w) => (
                        <li key={w}>{w}</li>
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
                      disabled={!m.title?.trim() || busy !== null}
                      onClick={() => void save(item.external_id, results.catalog.catalog_id)}
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
              disabled={busy !== null || results.page === 1}
              onClick={() => void search(results.page - 1, results.keyword, results.limit)}
            >
              Previous results
            </button>
            <button
              disabled={busy !== null || results.next_page === null}
              onClick={() => {
                if (results.next_page !== null)
                  void search(results.next_page, results.keyword, results.limit);
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
