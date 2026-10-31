import { useEffect, useRef, useState } from 'react';
import { Link, useBlocker } from 'react-router-dom';
import { tenderciteConfigured } from '../api/tendercite';
import { DiscoveryError, importTed, searchTed, type TedSearchResult } from '../api/discovery';
import type { OpportunityRecord } from '../api/opportunities';
import { Empty, Badge } from '../components/ui';
import { BkmsDiscovery } from './BkmsDiscovery';

type Outcome = {
  status?: 'CREATED' | 'ALREADY_PRESENT';
  record?: OpportunityRecord;
  error?: string;
};
const warnings: Record<string, string> = {
  MISSING_TITLE: 'TED did not supply a title; this result cannot be imported.',
  MISSING_BUYER: 'Buyer not supplied.',
  MISSING_DESCRIPTION: 'Summary not supplied.',
  MISSING_PUBLICATION_DATE: 'Publication date not supplied.',
  MISSING_DEADLINE: 'Submission date not supplied.',
  MULTIPLE_TENDER_DEADLINES: 'Several lot submission dates exist; no single deadline was chosen.',
  TITLE_SHORTENED: 'Title shortened to 200 characters.',
  ORGANIZATION_SHORTENED: 'Buyer labels shortened to 200 characters.',
  SUMMARY_SHORTENED: 'Summary shortened to 2,000 characters.',
};
export function Discover() {
  const [source, setSource] = useState('TED');
  const [busy, setBusy] = useState(false);
  return (
    <>
      <section className="panel">
        <h1>Discover Opportunities</h1>
        <p>
          Inspect official procurement metadata and explicitly save selected opportunities. TED
          provides remote search; German Open Data uses one explicitly downloaded daily catalog. No
          documents, skills, legal requirements or qualifications are inferred.
        </p>
        <p>
          TED uses TENDERCITE_DISCOVERY_API_ENABLED; German Open Data uses the separate
          TENDERCITE_BKMS_DISCOVERY_API_ENABLED flag. The opportunities flag enables
          browsing/editing saved records. No API key is required for public TED search. These
          development flags are not authentication.
        </p>
      </section>
      <section className="panel">
        <label>
          Discovery source
          <select value={source} disabled={busy} onChange={(e) => setSource(e.target.value)}>
            <option value="TED">TED — European procurement notices</option>
            <option value="BKMS">German Bekanntmachungsservice — public procurement</option>
          </select>
        </label>
        <p>Switching sources never searches, imports or downloads provider data automatically.</p>
      </section>
      {tenderciteConfigured() ? (
        source === 'TED' ? (
          <DiscoveryWorkspace onBusyChange={setBusy} />
        ) : (
          <BkmsDiscovery onBusyChange={setBusy} />
        )
      ) : (
        <Empty>
          TenderCite is not configured. Set the fixed local development proxy and restart Vite.
        </Empty>
      )}
    </>
  );
}
function DiscoveryWorkspace({ onBusyChange }: { onBusyChange: (busy: boolean) => void }) {
  const [keyword, setKeyword] = useState('');
  const [limit, setLimit] = useState(10);
  const [result, setResult] = useState<TedSearchResult | null>(null);
  const [error, setError] = useState('');
  const [searching, setSearching] = useState(false);
  const [saving, setSaving] = useState<string | null>(null);
  const [outcomes, setOutcomes] = useState<Record<string, Outcome>>({});
  const active = useRef<AbortController | null>(null);
  const importing = useRef<string | null>(null);
  const alive = useRef(true);
  const blocker = useBlocker(() => importing.current !== null);
  useEffect(() => {
    alive.current = true;
    function unload(event: BeforeUnloadEvent) {
      if (importing.current) {
        event.preventDefault();
        event.returnValue = '';
      }
    }
    window.addEventListener('beforeunload', unload);
    return () => {
      alive.current = false;
      active.current?.abort();
      window.removeEventListener('beforeunload', unload);
    };
  }, []);
  async function search(page = 1, phrase = keyword, size = limit) {
    if (active.current || importing.current) return;
    const controller = new AbortController();
    active.current = controller;
    onBusyChange(true);
    setSearching(true);
    setError('');
    try {
      const response = await searchTed(phrase, page, size, controller.signal);
      if (alive.current && active.current === controller) setResult(response);
    } catch (failure) {
      if (alive.current && !controller.signal.aborted) {
        setResult(null);
        setError(
          failure instanceof DiscoveryError
            ? failure.message
            : 'TED search unavailable. No automatic retry was made.',
        );
      }
    } finally {
      if (active.current === controller) {
        active.current = null;
        if (alive.current) setSearching(false);
        if (alive.current) onBusyChange(false);
      }
    }
  }
  async function save(id: string) {
    if (importing.current || active.current || outcomes[id]?.record) return;
    importing.current = id;
    onBusyChange(true);
    setSaving(id);
    try {
      const outcome = await importTed(id);
      if (alive.current) setOutcomes((old) => ({ ...old, [id]: outcome }));
    } catch (failure) {
      if (alive.current)
        setOutcomes((old) => ({
          ...old,
          [id]: {
            error: `${failure instanceof DiscoveryError ? failure.message : 'Import response unavailable.'} Save not confirmed: a record may already exist. Check Opportunities before explicitly retrying; imports deduplicate by TED publication. No automatic retry was made.`,
          },
        }));
    } finally {
      importing.current = null;
      if (alive.current) setSaving(null);
      if (alive.current) onBusyChange(false);
    }
  }
  return (
    <>
      {blocker.state === 'blocked' && (
        <aside role="alertdialog" aria-label="TED import in progress" className="panel">
          <p>
            Wait for the import response before leaving. Closing the page can hide an uncertain save
            outcome.
          </p>
          <button autoFocus onClick={() => blocker.reset()}>
            Stay on Discover
          </button>
          <button disabled={saving !== null} onClick={() => blocker.proceed()}>
            Continue navigation
          </button>
        </aside>
      )}
      <section className="panel" aria-label="TED search">
        <form
          onSubmit={(event) => {
            event.preventDefault();
            void search();
          }}
        >
          <fieldset disabled={searching || saving !== null}>
            <legend>Search official TED metadata</legend>
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
              Search
            </button>
          </fieldset>
        </form>
        <p>
          Literal words/phrases only; no advanced expert query syntax. Searches are explicit, with
          no background monitoring.
        </p>
        {searching && <p role="status">Searching TED…</p>}
        {error && <p role="alert">{error}</p>}
      </section>
      {result && (
        <section className="panel" aria-label="TED search results">
          <h2>
            Results for “{result.keyword}” · page {result.page}
          </h2>
          <p>
            TED reported {result.total_notice_count} matches at search time. Pages can change as
            notices are published; this is not an exhaustive or stable dataset.
          </p>
          {result.pagination_capped && (
            <p>
              This local pilot offers at most 50 pages. Narrow your keyword to inspect more relevant
              notices.
            </p>
          )}
          {!result.items.length && <Empty>TED returned no results for this keyword.</Empty>}
          <ul className="discovery-results">
            {result.items.map((item) => {
              const outcome = outcomes[item.publication_id];
              return (
                <li className="panel" key={item.publication_id}>
                  <h3>{item.title ?? 'Title not supplied by TED'}</h3>
                  <Badge>TED · {item.publication_id}</Badge>
                  <p>Organization: {item.organization ?? 'Not supplied by TED'}</p>
                  <p>
                    Submission date (date only):{' '}
                    {item.submission_deadline ?? 'No single date supplied'}
                  </p>
                  <p className="source-page-text">
                    {item.summary
                      ? `${item.summary.slice(0, 300)}${item.summary.length > 300 ? '…' : ''}`
                      : 'Summary not supplied by TED.'}
                  </p>
                  <details>
                    <summary>Inspect TED result</summary>
                    <p>Publication date: {item.publication_date ?? 'Not supplied'}</p>
                    <p className="source-page-text">{item.summary ?? 'No summary supplied.'}</p>
                    <p>
                      Metadata retrieved: {item.retrieved_at}. Source metadata does not verify a
                      tender interpretation or bidder eligibility.
                    </p>
                    <ul>
                      {item.warnings.map((code) => (
                        <li key={code}>{warnings[code]}</li>
                      ))}
                    </ul>
                  </details>
                  <p>
                    <a
                      href={item.source_url}
                      target="_blank"
                      rel="noopener noreferrer"
                      referrerPolicy="no-referrer"
                    >
                      Original TED publication
                    </a>
                  </p>
                  {!outcome?.record && (
                    <button
                      disabled={!item.importable || searching || saving !== null}
                      onClick={() => void save(item.publication_id)}
                    >
                      Save to Opportunities
                    </button>
                  )}
                  {saving === item.publication_id && (
                    <p role="status">Importing selected TED publication…</p>
                  )}
                  {outcome?.error && (
                    <p role="alert">
                      {outcome.error} <Link to="/opportunities">View Opportunities</Link>
                    </p>
                  )}
                  {outcome?.record && (
                    <aside aria-label={`Saved TED publication ${item.publication_id}`}>
                      <p role="status">
                        Server confirmed {outcome.status}.{' '}
                        {outcome.status === 'ALREADY_PRESENT'
                          ? 'The existing opportunity and its local edits were retained.'
                          : 'A native opportunity was saved with TED attribution and no inferred skills.'}
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
              disabled={searching || saving !== null || result.page === 1}
              onClick={() => void search(result.page - 1, result.keyword, result.limit)}
            >
              Previous results
            </button>
            <button
              disabled={searching || saving !== null || result.next_page === null}
              onClick={() => {
                if (result.next_page !== null)
                  void search(result.next_page, result.keyword, result.limit);
              }}
            >
              Next results
            </button>
          </div>
          <p>
            Saving rechecks the selected publication with TED. Imported metadata stays NOT_VERIFIED;
            skills and any subsequent pipeline action remain manual.
          </p>
        </section>
      )}
    </>
  );
}
