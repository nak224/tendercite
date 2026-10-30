import type { ReactNode } from 'react';
import { Link } from 'react-router-dom';
import { useQuery, type UseQueryResult } from '@tanstack/react-query';
import { tenderciteConfigured } from '../api/tendercite';
import { listProfiles } from '../api/profiles';
import { listOpportunities } from '../api/opportunities';
import { dashboardPipeline } from '../api/dashboard';
import { actionDateLabel, localCalendarDate } from '../utils/actionDate';
import { Badge, Empty } from '../components/ui';

const PREVIEW_ITEMS = 4;
function Preview<T>({
  name,
  query,
  children,
}: {
  name: string;
  query: UseQueryResult<T, Error>;
  children: (data: T) => ReactNode;
}) {
  if (query.isPending) return <p role="status">Loading {name} preview…</p>;
  if (query.isError)
    return (
      <div role="alert" className="notice danger">
        <p>
          {name} preview unavailable. Its local API may be disabled or unreachable. Other sections
          remain available.
        </p>
        <button onClick={() => void query.refetch()}>Reload {name} preview</button>
      </div>
    );
  return (
    <>
      {query.isFetching && <p role="status">Refreshing {name} preview…</p>}
      {children(query.data)}
    </>
  );
}
function PreviewScope({ loaded, more }: { loaded: number; more: boolean }) {
  return (
    <p className="dashboard-scope">
      Showing {Math.min(loaded, PREVIEW_ITEMS)} of {loaded} loaded records from the first page.{' '}
      {more ? 'More saved records are available.' : 'No additional page was reported.'} This is a
      bounded preview; open the full page to browse records.
    </p>
  );
}
export function Dashboard() {
  return (
    <>
      <section className="panel dashboard-welcome">
        <p className="dashboard-eyebrow">YOUR LOCAL WORKSPACE</p>
        <h1>TenderCite Dashboard</h1>
        <p>
          Record capabilities, explore your saved external opportunities and track what to do next.
          Start with a profile and an opportunity, compare their declared skills, then explicitly
          track progress.
        </p>
        <div className="actions" role="group" aria-label="Quick actions">
          <Link className="button primary" to="/profiles">
            Create Profile
          </Link>
          <Link className="button" to="/opportunities">
            Add Opportunity
          </Link>
          <Link className="button" to="/matching">
            Compare Skills
          </Link>
          <Link className="button" to="/pipeline">
            View Pipeline
          </Link>
        </div>
        <p className="dashboard-scope">
          Actions open existing pages. Nothing is compared, saved, submitted or approved
          automatically. Current native workflows use manual declarations and deterministic label
          comparison; AI discovery and recommendations are future work.
        </p>
      </section>
      {tenderciteConfigured() ? (
        <NativeOverview />
      ) : (
        <section className="panel" role="status">
          <h2>Local backend not configured</h2>
          <p>
            Configure the fixed TenderCite development proxy to load saved previews. You can still
            explore the workspace navigation.
          </p>
        </section>
      )}
      <section className="panel" aria-labelledby="dashboard-tender-analysis">
        <h2 id="dashboard-tender-analysis">Tender Analysis</h2>
        <p>
          Work separately with PDF documents, source quotations, findings and human review. A
          manually recorded opportunity has not automatically undergone document analysis.
        </p>
        <div className="actions">
          <Link className="button" to="/tenders">
            Documents &amp; findings
          </Link>
          <Link className="button" to="/tenders/intake">
            Manual requirements
          </Link>
          <Link className="button" to="/tenders/skill-mapping">
            Skill Mapping
          </Link>
          <Link className="button" to="/tenders/capability-report">
            Capability Matching
          </Link>
        </div>
        <p className="dashboard-scope">
          Workforce snapshots and approved mappings belong to the separate capability workflow. They
          are not required for native profiles, opportunities, matching or pipeline tracking.
        </p>
      </section>
    </>
  );
}
function NativeOverview() {
  const options = {
    retry: false,
    gcTime: 0,
    staleTime: 0,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
  } as const;
  const profiles = useQuery({
    ...options,
    queryKey: ['tendercite', 'dashboard', 'profiles'],
    queryFn: ({ signal }) => listProfiles(0, signal),
  });
  const opportunities = useQuery({
    ...options,
    queryKey: ['tendercite', 'dashboard', 'opportunities'],
    queryFn: ({ signal }) => listOpportunities(0, signal),
  });
  const pipeline = useQuery({
    ...options,
    queryKey: ['tendercite', 'dashboard', 'pipeline'],
    queryFn: ({ signal }) => dashboardPipeline(signal),
  });
  const today = localCalendarDate(new Date());
  return (
    <>
      <div className="dashboard-refresh">
        <button
          disabled={profiles.isFetching || opportunities.isFetching || pipeline.isFetching}
          onClick={() => {
            void profiles.refetch();
            void opportunities.refetch();
            void pipeline.refetch();
          }}
        >
          Refresh previews
        </button>
      </div>
      <div className="dashboard-grid">
        <section className="panel" aria-labelledby="dashboard-profiles">
          <h2 id="dashboard-profiles">Profiles</h2>
          <p>Your manually declared freelancer and company capabilities.</p>
          <Preview name="Profiles" query={profiles}>
            {(data) => (
              <>
                {!data.profiles.length && (
                  <Empty>No saved profiles in this preview. Create a profile to begin.</Empty>
                )}
                <ul className="dashboard-records">
                  {data.profiles.slice(0, PREVIEW_ITEMS).map((p) => (
                    <li key={p.profile_id}>
                      <h3>{p.name}</h3>
                      <Badge>{p.profile_type}</Badge>
                    </li>
                  ))}
                </ul>
                <PreviewScope loaded={data.profiles.length} more={data.next_offset !== null} />
              </>
            )}
          </Preview>
          <Link to="/profiles">Manage profiles</Link>
        </section>
        <section className="panel" aria-labelledby="dashboard-opportunities">
          <h2 id="dashboard-opportunities">Opportunities</h2>
          <p>Manually recorded external references, not verified tender analyses.</p>
          <Preview name="Opportunities" query={opportunities}>
            {(data) => (
              <>
                {!data.opportunities.length && (
                  <Empty>
                    No saved opportunities in this preview. Add an external project or tender
                    reference.
                  </Empty>
                )}
                <ul className="dashboard-records">
                  {data.opportunities.slice(0, PREVIEW_ITEMS).map((o) => (
                    <li key={o.opportunity_id}>
                      <h3>{o.title}</h3>
                      <Badge>{o.opportunity_type}</Badge>
                      <p>{o.organization || 'Organization not specified'}</p>
                      {o.deadline && (
                        <p>
                          Submission deadline (date only):{' '}
                          <time dateTime={o.deadline}>{o.deadline}</time>
                        </p>
                      )}
                      <div className="actions">
                        <Link to={`/matching?opportunity_id=${o.opportunity_id}`}>
                          Compare this opportunity
                        </Link>
                        <Link to={`/pipeline?opportunity_id=${o.opportunity_id}`}>
                          Track this opportunity
                        </Link>
                      </div>
                    </li>
                  ))}
                </ul>
                <PreviewScope loaded={data.opportunities.length} more={data.next_offset !== null} />
              </>
            )}
          </Preview>
          <Link to="/opportunities">Manage opportunities</Link>
        </section>
        <section className="panel dashboard-pipeline" aria-labelledby="dashboard-pipeline">
          <h2 id="dashboard-pipeline">Pipeline</h2>
          <p>
            Continue manual acquisition tracking. Stages and outcomes are operator declarations.
          </p>
          <p className="dashboard-scope">
            Action date labels compare calendar dates with your device's local date ({today}).
            Closed stages retain dates without overdue labels. No precise time, completed action or
            notification is inferred.
          </p>
          <Preview name="Pipeline" query={pipeline}>
            {(data) => (
              <>
                {!data.entries.length && (
                  <Empty>
                    No tracked opportunities in this preview. Add a saved opportunity explicitly in
                    Pipeline.
                  </Empty>
                )}
                <ul className="dashboard-records dashboard-pipeline-records">
                  {data.entries.slice(0, PREVIEW_ITEMS).map((e) => {
                    const dateLabel = actionDateLabel(e.next_action_due_date, e.stage, today);
                    return (
                      <li key={e.pipeline_id}>
                        <h3>{e.title}</h3>
                        <div className="actions">
                          <Badge>{e.stage}</Badge>
                          <Badge>Priority {e.priority}</Badge>
                        </div>
                        <p className="source-page-text">
                          Next action: {e.next_action || 'Not recorded'}
                        </p>
                        <p>
                          <Badge tone={dateLabel === 'Overdue action date' ? 'danger' : ''}>
                            {dateLabel}
                          </Badge>{' '}
                          {e.next_action_due_date && (
                            <time dateTime={e.next_action_due_date}>{e.next_action_due_date}</time>
                          )}
                        </p>
                        <Link to={`/pipeline?pipeline_id=${e.pipeline_id}`}>
                          Open existing pipeline entry
                        </Link>
                      </li>
                    );
                  })}
                </ul>
                <PreviewScope loaded={data.entries.length} more={data.next_offset !== null} />
              </>
            )}
          </Preview>
          <Link to="/pipeline">Open full Pipeline</Link>
        </section>
      </div>
    </>
  );
}
