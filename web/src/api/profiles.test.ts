import { afterEach, expect, test, vi } from 'vitest';
import { getProfile, listProfiles, profileInput, saveProfile, emptyProfile } from './profiles';
import { profileId, profileRecord } from '../test/profiles';

afterEach(() => vi.unstubAllGlobals());
const signal = () => new AbortController().signal;
const mock = (reply: (request: Request) => Response | Promise<Response>) => {
  const fetcher = vi.fn(async (request: Request) => reply(request));
  vi.stubGlobal('fetch', fetcher);
  return fetcher;
};
test('create and update use exact versioned bodies, local fixed transport and optimistic revision', async () => {
  const input = profileRecord().profile;
  const fetcher = mock(async (request) => {
    expect(new URL(request.url).origin).toBe(window.location.origin);
    expect(request.redirect).toBe('error');
    expect(request.credentials).toBe('omit');
    expect(request.cache).toBe('no-store');
    const body = await request.json();
    const update = request.method === 'PUT';
    expect(new URL(request.url).pathname).toBe(
      `/tendercite-api/api/v1/profiles${update ? '/' + profileId : ''}`,
    );
    expect(body).toEqual(
      update
        ? { schema_version: 'native-profile-update-1', expected_revision: 1, profile: input }
        : { schema_version: 'native-profile-create-1', profile_id: profileId, profile: input },
    );
    return Response.json({
      schema_version: 'native-profile-save-result-1',
      status: update ? 'UPDATED' : 'CREATED',
      record: { ...profileRecord(), revision: update ? 2 : 1 },
    });
  });
  expect((await saveProfile(profileId, input)).status).toBe('CREATED');
  expect((await saveProfile(profileId, input, 1)).record.revision).toBe(2);
  expect(fetcher).toHaveBeenCalledTimes(2);
});
test('identical creation can return ALREADY_PRESENT while still using one explicit request', async () => {
  const fetcher = mock(() =>
    Response.json({
      schema_version: 'native-profile-save-result-1',
      status: 'ALREADY_PRESENT',
      record: profileRecord(),
    }),
  );
  expect((await saveProfile(profileId, profileRecord().profile)).status).toBe('ALREADY_PRESENT');
  expect(fetcher).toHaveBeenCalledTimes(1);
});
test.each([
  'name',
  'type',
  'arrangement',
  'duplicate-name',
  'duplicate-id',
  'skills-budget',
  'keyword-budget',
  'verified',
])('invalid %s input fails locally without a write', async (kind) => {
  const input = profileRecord().profile;
  if (kind === 'name') input.name = ' ';
  if (kind === 'type') Object.assign(input, { profile_type: 'PRIVATE' });
  if (kind === 'arrangement') Object.assign(input, { work_arrangement: 'PRIVATE' });
  if (kind === 'duplicate-name')
    input.skills.push({
      ...input.skills[0]!,
      skill_id: crypto.randomUUID(),
      name: ' ＰＹＴＨＯＮ ',
    });
  if (kind === 'duplicate-id') input.skills.push({ ...input.skills[0]!, name: 'React' });
  if (kind === 'skills-budget')
    input.skills = Array.from({ length: 101 }, (_, i) => ({
      ...input.skills[0]!,
      skill_id: crypto.randomUUID(),
      name: String(i),
    }));
  if (kind === 'keyword-budget')
    input.opportunity_keywords = Array.from({ length: 21 }, (_, i) => String(i));
  if (kind === 'verified') Object.assign(input, { qualification: 'VERIFIED' });
  const fetcher = mock(() => Response.json({}));
  await expect(saveProfile(profileId, input)).rejects.toThrow(/Enter a profile name/);
  expect(fetcher).not.toHaveBeenCalled();
});
test('normalization trims operator fields while preserving internal skill identities', () => {
  const data = profileRecord().profile;
  data.name = '  Synthetic freelancer ';
  data.skills[0]!.name = ' Python ';
  expect(profileInput(data)).toEqual(profileRecord().profile);
  expect(
    profileInput({ ...emptyProfile(), name: 'Company', profile_type: 'COMPANY' }).skills,
  ).toEqual([]);
});
test.each(['schema', 'identity', 'revision', 'content', 'provenance'])(
  'a mismatched %s save response is not a confirmed save',
  async (kind) => {
    const record = profileRecord();
    if (kind === 'schema') Object.assign(record, { schema_version: 'PRIVATE' });
    if (kind === 'identity') record.profile_id = crypto.randomUUID();
    if (kind === 'revision') record.revision = 3;
    if (kind === 'content') record.profile.name = 'Other';
    if (kind === 'provenance')
      Object.assign(record.profile, { source_origin: 'team-capacity-engine' });
    mock(() =>
      Response.json({ schema_version: 'native-profile-save-result-1', status: 'CREATED', record }),
    );
    await expect(saveProfile(profileId, profileRecord().profile)).rejects.toThrow(
      /Invalid or mismatched profile save response/,
    );
  },
);
test.each(['PROFILES_API_DISABLED', 'PROFILE_REVISION_CONFLICT', 'PROFILE_ID_CONFLICT', 'PRIVATE'])(
  'HTTP errors expose only safe codes (%s) and never retry',
  async (code) => {
    const fetcher = mock(() =>
      Response.json({ detail: { code, message: 'PRIVATE' } }, { status: 409 }),
    );
    await expect(saveProfile(profileId, profileRecord().profile)).rejects.toThrow(
      `Profile request failed (HTTP 409)${code === 'PRIVATE' ? '' : ': ' + code}.`,
    );
    expect(fetcher).toHaveBeenCalledTimes(1);
  },
);
test.each(['network', 'html', 'malformed'])(
  '%s failures do not log or echo response content',
  async (kind) => {
    const fetcher = mock(() => {
      if (kind === 'network') throw Error('PRIVATE');
      return new Response('PRIVATE', {
        headers: { 'Content-Type': kind === 'html' ? 'text/html' : 'application/json' },
      });
    });
    await expect(saveProfile(profileId, profileRecord().profile)).rejects.toThrow(
      /save response unavailable or invalid/,
    );
    expect(fetcher).toHaveBeenCalledTimes(1);
  },
);
test('read and paginated listing validate identities and discard extra content', async () => {
  const record = profileRecord();
  const fetcher = mock((request) =>
    Response.json(
      request.url.includes('?')
        ? {
            schema_version: 'native-profile-listing-1',
            next_offset: null,
            profiles: [
              {
                profile_id: record.profile_id,
                revision: 1,
                name: record.profile.name,
                profile_type: 'FREELANCER',
                updated_at: record.updated_at,
                notes: 'PRIVATE',
              },
            ],
          }
        : { ...record, notes: 'PRIVATE' },
    ),
  );
  expect(JSON.stringify(await listProfiles(0, signal()))).not.toContain('PRIVATE');
  expect(fetcher.mock.calls[0]![0].url).toMatch(/profiles\?limit=50&offset=0$/);
  expect(await getProfile(profileId, signal())).toEqual(record);
  mock(() => Response.json({ ...record, profile_id: crypto.randomUUID() }));
  await expect(getProfile(profileId, signal())).rejects.toThrow(/mismatched profile response/);
});
