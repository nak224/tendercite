import { useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Link, Route, Routes, useParams, useNavigate } from 'react-router-dom';
import { ManualIntake } from './ManualIntake';
import { ImportWorkforceSnapshot } from './ImportWorkforceSnapshot';
import { SkillMapping } from './SkillMapping';
import { TenderAssessment } from './TenderAssessment';
import { FindingReview } from '../components/FindingReview';
import { findingForReview } from '../api/tenderciteReviews';
import { TenderUpload } from '../components/TenderUpload';
import { Async, Empty, Badge } from '../components/ui';
import {
  tenderciteConfigured,
  tenderGet,
  tenderPage,
  type TenderDocument,
  type TenderFinding,
  type TenderAnalysis,
} from '../api/tendercite';

const idPath = encodeURIComponent;
function useTender<T>(path: string) {
  return useQuery({
    queryKey: ['tendercite', path],
    queryFn: ({ signal }) => tenderGet<T>(path, signal),
    retry: false,
    gcTime: 0,
  });
}
function FindingList({ analysisId }: { analysisId?: string }) {
  const query = useTender<TenderFinding[]>(
    `findings${analysisId ? `?analysis_run_id=${idPath(analysisId)}` : ''}`,
  );
  return (
    <Async query={query}>
      {(findings) => (
        <>
          {!analysisId && (
            <>
              <h2>Analyses referenced by findings</h2>
              <p>
                No analysis-list API is available; analyses without findings are not discoverable
                here.
              </p>
              <ul>
                {[...new Set(findings.map((f) => f.analysis_run_id).filter(Boolean))]
                  .sort()
                  .map((id) => (
                    <li key={id}>
                      <Link to={`/tenders/analyses/${idPath(id)}`}>{id}</Link>
                    </li>
                  ))}
              </ul>
            </>
          )}
          <h2 id="current-findings">Current findings</h2>
          {findings.length ? (
            <ul>
              {findings.map((f) => (
                <li key={f.id}>
                  <Link to={`/tenders/findings/${idPath(f.id)}`}>
                    {f.effective_value.statement}
                  </Link>{' '}
                  <Badge>{f.review_status}</Badge>
                </li>
              ))}
            </ul>
          ) : (
            <Empty>No findings available.</Empty>
          )}
        </>
      )}
    </Async>
  );
}
function TenderHome() {
  const documents = useTender<TenderDocument[]>('documents');
  const [analysisId, setAnalysisId] = useState('');
  const navigate = useNavigate();
  return (
    <>
      <section className="panel">
        <h2>Manual Requirement Intake</h2>
        <p>
          Create source-backed candidate requirements from existing PDFs, then validate and
          explicitly save them.
        </p>
        <Link className="button" to="/tenders/intake">
          Create manual requirements
        </Link>
      </section>
      <TenderUpload />
      <section className="panel">
        <h2>Documents</h2>
        <Async query={documents}>
          {(docs) =>
            docs.length ? (
              <ul>
                {docs.map((d) => (
                  <li key={d.id}>
                    <Link to={`/tenders/documents/${idPath(d.id)}`}>{d.filename}</Link> ·{' '}
                    {d.page_count} pages · {d.chunk_count} chunks
                  </li>
                ))}
              </ul>
            ) : (
              <Empty>No documents available.</Empty>
            )
          }
        </Async>
      </section>
      <section className="panel">
        <form
          onSubmit={(event) => {
            event.preventDefault();
            if (analysisId.trim()) void navigate(`/tenders/analyses/${idPath(analysisId.trim())}`);
          }}
        >
          <label>
            Known analysis ID
            <input
              value={analysisId}
              onChange={(event) => setAnalysisId(event.target.value)}
              maxLength={200}
              required
            />
          </label>
          <button type="submit">Open analysis</button>
        </form>
        <FindingList />
      </section>
    </>
  );
}
function DocumentDetail() {
  const { id = '' } = useParams();
  const query = useTender<TenderDocument>(`documents/${idPath(id)}`);
  return (
    <Async query={query}>
      {(d) => (
        <section className="panel">
          <h2>{d.filename}</h2>
          <p>Document ID: {d.id}</p>
          <p>SHA-256: {d.sha256}</p>
          <p>
            {d.page_count} pages · {d.chunk_count} chunks · {d.created_at}
          </p>
        </section>
      )}
    </Async>
  );
}
function FindingDetail() {
  const { id = '' } = useParams();
  const client = useQueryClient();
  const query = useQuery({
    queryKey: ['tendercite', `findings/${idPath(id)}`],
    queryFn: ({ signal }) => findingForReview(id, signal),
    retry: false,
    gcTime: 0,
  });
  const f = query.data;
  if (!f) return <Async query={query}>{() => null}</Async>;
  async function refreshFinding() {
    await Promise.all([
      query.refetch({ throwOnError: true }),
      client.invalidateQueries({
        predicate: (entry) =>
          entry.queryKey[0] === 'tendercite' &&
          typeof entry.queryKey[1] === 'string' &&
          (entry.queryKey[1] === 'findings' ||
            entry.queryKey[1].startsWith('findings?') ||
            entry.queryKey[1] === `analyses/${idPath(f!.analysis_run_id)}`),
      }),
    ]);
  }
  return (
    <>
      {query.isError && (
        <p role="alert">Could not reload this finding. Displayed state may be stale.</p>
      )}
      <section className="panel">
        <h2>{f.effective_value.statement}</h2>
        <p>
          {f.effective_value.category} · {f.effective_value.requirement_type}
        </p>
        <p>
          Review: {f.review_status} · Grounding: {f.grounding_status}
        </p>
        <p>Original statement: {f.statement}</p>
        <p>
          Original classification: {f.category} · {f.requirement_type}
        </p>
        {f.analysis_run_id && (
          <Link to={`/tenders/analyses/${idPath(f.analysis_run_id)}`}>
            Analysis {f.analysis_run_id}
          </Link>
        )}
        <h3>Source evidence</h3>
        {f.evidence.length ? (
          f.evidence.map((e, i) => (
            <div key={i}>
              <Link to={`/tenders/documents/${idPath(e.document_id)}`}>
                Document {e.document_id}
              </Link>
              <p>
                PDF page {e.page_number} · {e.grounding_status}
              </p>
              <blockquote>{e.quote}</blockquote>
              <Link to={`/tenders/documents/${idPath(e.document_id)}/pages/${e.page_number}`}>
                View source PDF page {e.page_number}
              </Link>
            </div>
          ))
        ) : (
          <Empty>No source evidence.</Empty>
        )}
      </section>
      <FindingReview
        key={f.id}
        finding={f}
        findingCurrent={!query.isError}
        refreshFinding={refreshFinding}
      />
    </>
  );
}
function SourcePage() {
  const { id = '', pageNumber = '' } = useParams();
  const number = Number(pageNumber);
  const valid = /^[1-9]\d*$/.test(pageNumber) && Number.isSafeInteger(number);
  const query = useQuery({
    queryKey: ['tendercite', 'source-page', id, number],
    queryFn: ({ signal }) => tenderPage(id, number, signal),
    enabled: valid,
    retry: false,
    gcTime: 0,
  });
  if (!valid) return <p role="alert">Invalid physical PDF page number.</p>;
  return (
    <section className="panel">
      <h2>Source PDF page {number}</h2>
      <Link to={`/tenders/documents/${idPath(id)}`}>Document {id}</Link>
      <p>
        Stored extracted text from physical PDF page {number} (1-based), not a printed page label.
        Quotation verification establishes text occurrence, not interpretation correctness.
      </p>
      <Async query={query}>
        {(page) => (
          <>
            {page.text === '' && <Empty>No extracted text is stored for this page.</Empty>}
            <pre className="source-page-text">{page.text}</pre>
          </>
        )}
      </Async>
    </section>
  );
}
function AnalysisDetail() {
  const { id = '' } = useParams();
  const query = useTender<TenderAnalysis>(`analyses/${idPath(id)}`);
  return (
    <Async query={query}>
      {(a) => (
        <section className="panel">
          <h2>Analysis {a.id}</h2>
          <p>
            {a.provider} · {a.model} · {a.created_at}
          </p>
          <Link to={`/tenders/analyses/${idPath(a.id)}/assessment`}>Go/No-Go Assessment</Link>
          {' · '}
          <Link to={`/tenders/skill-mapping?analysis_id=${idPath(a.id)}`}>
            Map reviewed requirements to skills
          </Link>
          <FindingList analysisId={a.id} />
        </section>
      )}
    </Async>
  );
}
export function Tenders() {
  return (
    <>
      <div className="page-heading">
        <div>
          <h1>Tenders</h1>
          <p>Local TenderCite document workspace.</p>
        </div>
        <div className="actions">
          <Link to="/tenders">All tenders</Link>
          <Link to="/tenders/workforce-snapshot/import">Import Workforce Snapshot</Link>
        </div>
      </div>
      <p className="notice muted">
        Source quotation verification does not establish semantic correctness, qualification or
        bidder eligibility. Human interpretation review is separate from fulfillment or
        qualification. A separately approved skill mapping records an operator association, not
        independently verified qualification.
      </p>
      <Routes>
        <Route
          path="*"
          element={
            !tenderciteConfigured() ? (
              <Empty>
                TenderCite is not configured. Set TENDERCITE_PROXY_TARGET for the Vite development
                server and restart it.
              </Empty>
            ) : (
              <Routes>
                <Route index element={<TenderHome />} />
                <Route path="intake" element={<ManualIntake />} />
                <Route path="skill-mapping" element={<SkillMapping />} />
                <Route path="workforce-snapshot/import" element={<ImportWorkforceSnapshot />} />
                <Route path="documents/:id" element={<DocumentDetail />} />
                <Route path="documents/:id/pages/:pageNumber" element={<SourcePage />} />
                <Route path="findings/:id" element={<FindingDetail />} />
                <Route path="analyses/:id" element={<AnalysisDetail />} />
                <Route path="analyses/:id/assessment" element={<TenderAssessment />} />
                <Route path="*" element={<p role="alert">TenderCite page not found.</p>} />
              </Routes>
            )
          }
        />
      </Routes>
    </>
  );
}
