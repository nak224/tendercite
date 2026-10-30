import { useEffect, useRef, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Async, Empty } from '../components/ui';
import { ProfileForm } from '../components/ProfileForm';
import { tenderciteConfigured } from '../api/tendercite';
import {
  emptyProfile,
  getProfile,
  listProfiles,
  saveProfile,
  ProfileInputError,
  ProfileRequestError,
  type NativeProfile,
  type ProfileRecord,
} from '../api/profiles';

function ProfileWorkspace() {
  const [offset, setOffset] = useState(0);
  const [id, setId] = useState('');
  const [record, setRecord] = useState<ProfileRecord | null>(null);
  const [initial, setInitial] = useState<NativeProfile | null>(null);
  const [version, setVersion] = useState(0);
  const [observed, setObserved] = useState<ProfileRecord | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const running = useRef(false);
  const alive = useRef(true);
  const read = useRef<AbortController | null>(null);
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
      read.current?.abort();
    };
  }, []);
  const listing = useQuery({
    queryKey: ['tendercite', 'native-profiles', offset],
    queryFn: ({ signal }) => listProfiles(offset, signal),
    retry: false,
    gcTime: 0,
    refetchOnWindowFocus: false,
  });
  function load(value: ProfileRecord) {
    setId(value.profile_id);
    setRecord(value);
    setInitial(value.profile);
    setObserved(null);
    setVersion((v) => v + 1);
  }
  async function open(profileId: string) {
    if (running.current) return;
    running.current = true;
    setBusy(true);
    setError('');
    setNotice('');
    const controller = new AbortController();
    read.current = controller;
    try {
      const value = await getProfile(profileId, controller.signal);
      if (alive.current) load(value);
    } catch (failure) {
      if (alive.current)
        setError(failure instanceof ProfileRequestError ? failure.message : 'Cannot load profile.');
    } finally {
      running.current = false;
      if (alive.current) setBusy(false);
    }
  }
  async function checkCurrent() {
    const controller = new AbortController();
    read.current = controller;
    try {
      const value = await getProfile(id, controller.signal);
      if (alive.current) setObserved(value);
    } catch {
      if (alive.current)
        setNotice(
          'Current saved version could not be read. Your form is retained; check again before choosing whether to save.',
        );
    }
  }
  async function inspectCurrent() {
    if (running.current || !id) return;
    running.current = true;
    setBusy(true);
    try {
      await checkCurrent();
    } finally {
      running.current = false;
      if (alive.current) setBusy(false);
    }
  }
  async function save(profile: NativeProfile) {
    if (running.current || !id) return;
    running.current = true;
    setBusy(true);
    setError('');
    setNotice('');
    setObserved(null);
    try {
      const result = await saveProfile(id, profile, record?.revision);
      if (alive.current) {
        load(result.record);
        setNotice(
          `Server confirmed ${result.status}. Profile saved at revision ${result.record.revision}. Skills remain unverified.`,
        );
      }
    } catch (failure) {
      if (alive.current) {
        setError(
          failure instanceof ProfileInputError
            ? failure.message
            : `${failure instanceof ProfileRequestError ? failure.message : 'Profile save response unavailable.'} Save not confirmed: server state may have changed. No automatic retry was made. Your form is retained.`,
        );
        if (!(failure instanceof ProfileInputError)) await checkCurrent();
      }
    } finally {
      if (alive.current) {
        await listing.refetch();
        setBusy(false);
      }
      running.current = false;
    }
  }
  return (
    <>
      <section className="panel" aria-label="Saved profiles">
        <h2>Your profiles</h2>
        <p>
          Local, manually entered capabilities for a freelancer or company. No workforce snapshot or
          external application is required.
        </p>
        <p>
          Profile storage is local-development-only. If unavailable, explicitly enable
          TENDERCITE_PROFILES_API_ENABLED and restart the backend. The flag is not authentication.
        </p>
        <button
          type="button"
          disabled={busy}
          onClick={() => {
            if (running.current) return;
            setId(crypto.randomUUID());
            setRecord(null);
            setInitial(emptyProfile());
            setVersion((v) => v + 1);
            setError('');
            setNotice('');
            setObserved(null);
          }}
        >
          Create profile
        </button>
        <button
          type="button"
          disabled={busy || listing.isFetching}
          onClick={() => void listing.refetch()}
        >
          Reload profile list
        </button>
        <Async query={listing}>
          {(data) => (
            <>
              {!data.profiles.length && <Empty>No saved profiles yet.</Empty>}
              <ul>
                {data.profiles.map((p) => (
                  <li key={p.profile_id}>
                    <button type="button" disabled={busy} onClick={() => void open(p.profile_id)}>
                      Open {p.name}
                    </button>{' '}
                    · {p.profile_type} · Revision {p.revision}
                  </li>
                ))}
              </ul>
              <div className="actions">
                <button
                  type="button"
                  disabled={busy || offset === 0}
                  onClick={() => setOffset((n) => Math.max(0, n - 50))}
                >
                  Previous profiles
                </button>
                <button
                  type="button"
                  disabled={busy || data.next_offset === null}
                  onClick={() => {
                    if (data.next_offset !== null) setOffset(data.next_offset);
                  }}
                >
                  Next profiles
                </button>
              </div>
            </>
          )}
        </Async>
      </section>
      {busy && <p role="status">Saving or loading profile…</p>}
      {notice && <p role="status">{notice}</p>}
      {error && <p role="alert">{error}</p>}
      {initial && (
        <section className="panel" aria-label="Profile editor">
          <h2>{record ? 'Edit profile' : 'New profile'}</h2>
          {record && (
            <p>
              Saved revision: {record.revision} · Last updated: {record.updated_at}
            </p>
          )}
          <ProfileForm key={`${id}:${version}`} initial={initial} disabled={busy} save={save} />
          <button type="button" disabled={busy} onClick={() => void inspectCurrent()}>
            Check current saved version
          </button>
          {observed && (
            <aside aria-label="Current saved version">
              <p>
                The server currently has revision {observed.revision} of {observed.profile.name}.
                Unsaved form changes have not been applied to it.
              </p>
              <button
                type="button"
                disabled={busy}
                onClick={() => {
                  load(observed);
                  setError('');
                  setNotice('Loaded the current saved version. Make changes and save explicitly.');
                }}
              >
                Load current saved version (discard my edits)
              </button>
            </aside>
          )}
        </section>
      )}
      <p>
        Skills are user-declared, not independent evidence of qualification, certification,
        experience or bidder eligibility. Profiles do not run opportunity matching, capacity
        evaluation or Go/No-Go decisions. Keep permitted profile information private.
      </p>
    </>
  );
}
export function Profiles() {
  return (
    <>
      <div className="page-heading">
        <div>
          <h1>Profiles</h1>
          <p>Your manually declared capabilities and opportunity interests.</p>
        </div>
      </div>
      {tenderciteConfigured() ? (
        <ProfileWorkspace />
      ) : (
        <Empty>
          TenderCite is not configured. Set TENDERCITE_PROXY_TARGET for the Vite development server
          and restart it.
        </Empty>
      )}
    </>
  );
}
