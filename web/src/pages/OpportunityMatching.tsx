import { useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { tenderciteConfigured } from '../api/tendercite';
import { listProfiles } from '../api/profiles';
import { listOpportunities } from '../api/opportunities';
import { compareNativeSkills, NativeMatchError } from '../api/nativeMatching';
import type { NativeMatchResult } from '../api/nativeMatching';
import { Async } from '../components/ui';

export function OpportunityMatching() {
  return (
    <>
      <section className="panel">
        <h1>Opportunity Matching</h1>
        <p>
          Compare saved native profiles and opportunities using exact skill-label equality after
          Unicode NFKC normalization, case-folding and collapsed whitespace.
        </p>
        <p>
          Results compare manually entered skill labels only. They do not verify experience,
          qualifications, availability or eligibility. NOT_RECORDED means no equal declaration was
          found, not that a skill is absent.
        </p>
        <p>
          This is separate from workforce snapshot{' '}
          <Link to="/tenders/capability-report">Capability Matching</Link>. No ranking,
          qualification score or Go/No-Go decision is made.
        </p>
      </section>
      {tenderciteConfigured() ? (
        <Comparison />
      ) : (
        <p role="alert">
          TenderCite is not configured. Set the fixed local development proxy before comparing.
        </p>
      )}
    </>
  );
}

function Comparison() {
  const [profileOffset, setProfileOffset] = useState(0);
  const [opportunityOffset, setOpportunityOffset] = useState(0);
  const [profileId, setProfileId] = useState('');
  const [opportunityId, setOpportunityId] = useState('');
  // Private comparison output belongs only to this component, never query/browser storage.
  const [result, setResult] = useState<NativeMatchResult | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [refreshRequired, setRefreshRequired] = useState(false);
  const active = useRef<AbortController | null>(null);
  useEffect(
    () => () => {
      active.current?.abort();
      active.current = null;
    },
    [],
  );
  const options = {
    retry: false,
    gcTime: 0,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
  } as const;
  const profiles = useQuery({
    ...options,
    queryKey: ['tendercite', 'matching-profiles', profileOffset],
    queryFn: ({ signal }) => listProfiles(profileOffset, signal),
  });
  const opportunities = useQuery({
    ...options,
    queryKey: ['tendercite', 'matching-opportunities', opportunityOffset],
    queryFn: ({ signal }) => listOpportunities(opportunityOffset, signal),
  });
  const profile = profiles.data?.profiles.find((p) => p.profile_id === profileId);
  const opportunity = opportunities.data?.opportunities.find(
    (o) => o.opportunity_id === opportunityId,
  );
  function clear() {
    active.current?.abort();
    active.current = null;
    setBusy(false);
    setResult(null);
    setError('');
  }
  async function run() {
    if (
      active.current ||
      !profile ||
      !opportunity ||
      refreshRequired ||
      profiles.isFetching ||
      opportunities.isFetching
    )
      return;
    const controller = new AbortController();
    active.current = controller;
    setBusy(true);
    setResult(null);
    setError('');
    try {
      const output = await compareNativeSkills(profile, opportunity, controller.signal);
      if (active.current === controller && !controller.signal.aborted) setResult(output);
    } catch (failure) {
      if (active.current === controller && !controller.signal.aborted) {
        setError(
          failure instanceof NativeMatchError
            ? failure.message
            : 'Comparison unavailable. Refresh saved records.',
        );
        setRefreshRequired(true);
      }
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
        <p>
          Enable TENDERCITE_PROFILES_API_ENABLED, TENDERCITE_OPPORTUNITIES_API_ENABLED and the
          separate TENDERCITE_NATIVE_MATCHING_API_ENABLED flag on the local backend. All are
          disabled by default; flags are not authentication.
        </p>
        <div className="form-grid">
          <div>
            <Async query={profiles}>
              {(data) => (
                <>
                  <label>
                    Profile
                    <select
                      value={profileId}
                      onChange={(e) => {
                        clear();
                        setProfileId(e.target.value);
                      }}
                    >
                      <option value="">Select saved profile</option>
                      {data.profiles.map((p) => (
                        <option key={p.profile_id} value={p.profile_id}>
                          {p.name} · {p.profile_type} · revision {p.revision}
                        </option>
                      ))}
                    </select>
                  </label>
                  {!data.profiles.length && (
                    <p>
                      No saved profiles on this page. <Link to="/profiles">Create a profile</Link>.
                    </p>
                  )}
                  <button
                    disabled={!profileOffset}
                    onClick={() => {
                      clear();
                      setProfileId('');
                      setProfileOffset(profileOffset - 50);
                    }}
                  >
                    Previous profiles
                  </button>
                  <button
                    disabled={data.next_offset === null}
                    onClick={() => {
                      clear();
                      setProfileId('');
                      setProfileOffset(data.next_offset!);
                    }}
                  >
                    Next profiles
                  </button>
                </>
              )}
            </Async>
          </div>
          <div>
            <Async query={opportunities}>
              {(data) => (
                <>
                  <label>
                    Opportunity
                    <select
                      value={opportunityId}
                      onChange={(e) => {
                        clear();
                        setOpportunityId(e.target.value);
                      }}
                    >
                      <option value="">Select saved opportunity</option>
                      {data.opportunities.map((o) => (
                        <option key={o.opportunity_id} value={o.opportunity_id}>
                          {o.title} · {o.opportunity_type} · {o.organization || 'No organization'} ·
                          revision {o.revision}
                        </option>
                      ))}
                    </select>
                  </label>
                  {!data.opportunities.length && (
                    <p>
                      No saved opportunities on this page.{' '}
                      <Link to="/opportunities">Create an opportunity</Link>.
                    </p>
                  )}
                  <button
                    disabled={!opportunityOffset}
                    onClick={() => {
                      clear();
                      setOpportunityId('');
                      setOpportunityOffset(opportunityOffset - 50);
                    }}
                  >
                    Previous opportunities
                  </button>
                  <button
                    disabled={data.next_offset === null}
                    onClick={() => {
                      clear();
                      setOpportunityId('');
                      setOpportunityOffset(data.next_offset!);
                    }}
                  >
                    Next opportunities
                  </button>
                </>
              )}
            </Async>
          </div>
        </div>
        <button
          onClick={() => {
            clear();
            setRefreshRequired(false);
            setProfileId('');
            setOpportunityId('');
            void profiles.refetch();
            void opportunities.refetch();
          }}
        >
          Refresh saved records
        </button>
        <button
          disabled={
            busy ||
            refreshRequired ||
            !profile ||
            !opportunity ||
            !profiles.isSuccess ||
            !opportunities.isSuccess ||
            profiles.isFetching ||
            opportunities.isFetching
          }
          onClick={() => void run()}
        >
          Compare Skills
        </button>
        {busy && <p role="status">Comparing saved skill labels…</p>}
        {error && <p role="alert">{error}</p>}
        {refreshRequired && (
          <p>
            Refresh saved records and select current versions again. Comparison is never retried
            automatically.
          </p>
        )}
      </section>
      {result && (
        <section className="panel" aria-label="Skill comparison results">
          <h2>Comparison results</h2>
          <p>
            Profile: {result.profile.name} · {result.profile.profile_type} · revision{' '}
            {result.profile.revision}
          </p>
          <p>
            Opportunity: {result.opportunity.title} · {result.opportunity.opportunity_type} ·{' '}
            {result.opportunity.organization} · revision {result.opportunity.revision}
          </p>
          {result.status === 'NO_REQUIREMENTS' ? (
            <p>
              NO_REQUIREMENTS: This opportunity has no recorded required skills. No match judgment
              is possible.
            </p>
          ) : (
            <p>
              {result.counts.matched_skills} of {result.counts.total_opportunity_skills} requested
              skill labels are recorded in this profile.
            </p>
          )}
          <p>
            Total opportunity skills: {result.counts.total_opportunity_skills}. Matched:{' '}
            {result.counts.matched_skills}. Not recorded: {result.counts.not_recorded_skills}.
          </p>
          <h3>Profile declared skills</h3>
          {result.profile.skills.length ? (
            <ul>
              {result.profile.skills.map((s) => (
                <li key={s.skill_id}>
                  {s.original_label} → <code>{s.normalized_label}</code>
                </li>
              ))}
            </ul>
          ) : (
            <p>No declared profile skills.</p>
          )}
          <h3>Opportunity requested skills</h3>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Original required label</th>
                  <th>Normalized label</th>
                  <th>Comparison</th>
                  <th>Profile declaration</th>
                </tr>
              </thead>
              <tbody>
                {result.opportunity.required_skills.map((s, i) => (
                  <tr key={i}>
                    <td>{s.original_label}</td>
                    <td>
                      <code>{s.normalized_label}</code>
                    </td>
                    <td>{s.status}</td>
                    <td>
                      {s.matched_profile_skill_ids
                        .map(
                          (id) =>
                            result.profile.skills.find((p) => p.skill_id === id)!.original_label,
                        )
                        .join(', ') || 'No equal recorded declaration'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p>
            Algorithm: {result.algorithm_version}. Schema: {result.schema_version}.
          </p>
          <ul>
            {result.evidence_limitations.map((s, i) => (
              <li key={i}>{s}</li>
            ))}
          </ul>
          <p>
            Qualification: NOT_VERIFIED. Availability: NOT_EVALUATED. Eligibility: NOT_VERIFIED.
          </p>
        </section>
      )}
    </>
  );
}
