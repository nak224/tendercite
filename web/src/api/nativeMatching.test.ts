import { afterEach, expect, test, vi } from 'vitest';
import { compareNativeSkills } from './nativeMatching';
import { matchResult, profileMetadata, opportunityMetadata } from '../test/nativeMatching';
import { skillId } from '../test/profiles';

afterEach(() => vi.unstubAllGlobals());
const run = (signal = new AbortController().signal) =>
  compareNativeSkills(profileMetadata(), opportunityMetadata(), signal);

test('fixed-origin preview sends only saved identities and revisions, with safe transport', async () => {
  const fetcher = vi.fn(async (request: Request) => {
    expect(request.url).toBe(
      `${window.location.origin}/tendercite-api/api/v1/native-matches/preview`,
    );
    expect(request.method).toBe('POST');
    expect(request.cache).toBe('no-store');
    expect(request.credentials).toBe('omit');
    expect(request.redirect).toBe('error');
    expect(await request.json()).toEqual({
      schema_version: 'native-match-selection-1',
      profile_id: profileMetadata().profile_id,
      opportunity_id: opportunityMetadata().opportunity_id,
      expected_profile_revision: 1,
      expected_opportunity_revision: 1,
    });
    return Response.json({ ...matchResult(), private_records: 'PRIVATE' });
  });
  vi.stubGlobal('fetch', fetcher);
  expect(await run()).toEqual(matchResult());
  expect(fetcher).toHaveBeenCalledTimes(1);
});

test.each([
  'schema',
  'algorithm',
  'profile-id',
  'opportunity-id',
  'profile-revision',
  'opportunity-revision',
  'counts',
  'status',
  'qualification',
  'availability',
  'eligibility',
  'foreign-skill',
  'false-match',
  'false-not-recorded',
  'duplicate-profile',
  'duplicate-required',
  'normalized',
  'oversized',
  'limitations',
])('rejects inconsistent response: %s', async (kind) => {
  const result = matchResult();
  const raw = result as unknown as Record<string, unknown>;
  switch (kind) {
    case 'schema':
      raw.schema_version = 'PRIVATE';
      break;
    case 'algorithm':
      raw.algorithm_version = 'fuzzy';
      break;
    case 'profile-id':
      result.profile.profile_id = opportunityMetadata().opportunity_id;
      break;
    case 'opportunity-id':
      result.opportunity.opportunity_id = profileMetadata().profile_id;
      break;
    case 'profile-revision':
      result.profile.revision = 2;
      break;
    case 'opportunity-revision':
      result.opportunity.revision = 2;
      break;
    case 'counts':
      result.counts.matched_skills = 2;
      break;
    case 'status':
      result.status = 'NO_REQUIREMENTS';
      break;
    case 'qualification':
      raw.qualification = 'VERIFIED';
      break;
    case 'availability':
      raw.availability = 'AVAILABLE';
      break;
    case 'eligibility':
      raw.eligibility = 'VERIFIED';
      break;
    case 'foreign-skill':
      result.opportunity.required_skills[0]!.matched_profile_skill_ids = [
        opportunityMetadata().opportunity_id,
      ];
      break;
    case 'false-match':
      result.opportunity.required_skills[1]!.status = 'MATCHED';
      result.opportunity.required_skills[1]!.matched_profile_skill_ids = [skillId];
      break;
    case 'false-not-recorded':
      result.opportunity.required_skills[0]!.status = 'NOT_RECORDED';
      result.opportunity.required_skills[0]!.matched_profile_skill_ids = [];
      break;
    case 'duplicate-profile':
      result.profile.skills.push(result.profile.skills[0]!);
      break;
    case 'duplicate-required':
      result.opportunity.required_skills.push(result.opportunity.required_skills[0]!);
      break;
    case 'normalized':
      result.profile.skills[0]!.normalized_label = '';
      break;
    case 'oversized':
      result.profile.skills[0]!.original_label = 'a'.repeat(101);
      break;
    case 'limitations':
      result.evidence_limitations = [];
      break;
  }
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => Response.json(result)),
  );
  await expect(run()).rejects.toThrow('Invalid or mismatched comparison response');
});

test('server casefold labels are authoritative, including Unicode expansion', async () => {
  const result = matchResult();
  result.profile.skills[0]! = {
    skill_id: skillId,
    original_label: 'Straße',
    normalized_label: 'strasse',
  };
  result.opportunity.required_skills[0]!.original_label = 'STRASSE';
  result.opportunity.required_skills[0]!.normalized_label = 'strasse';
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => Response.json(result)),
  );
  expect((await run()).counts.matched_skills).toBe(1);
});

test.each(['NO_REQUIREMENTS', 'empty-profile'])('accepts explicit %s outcome', async (kind) => {
  const result = matchResult();
  if (kind === 'NO_REQUIREMENTS') {
    result.status = kind;
    result.opportunity.required_skills = [];
    result.counts = { total_opportunity_skills: 0, matched_skills: 0, not_recorded_skills: 0 };
  } else {
    result.profile.skills = [];
    result.opportunity.required_skills.forEach((s) => {
      s.status = 'NOT_RECORDED';
      s.matched_profile_skill_ids = [];
    });
    result.counts.matched_skills = 0;
    result.counts.not_recorded_skills = 2;
  }
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => Response.json(result)),
  );
  expect(await run()).toEqual(result);
});

test.each([
  'PROFILE_REVISION_CONFLICT',
  'OPPORTUNITY_REVISION_CONFLICT',
  'NATIVE_MATCHING_API_DISABLED',
  'PRIVATE',
])('safe error %s, no retry', async (code) => {
  const fetcher = vi.fn(async () =>
    Response.json({ detail: { code, message: 'PRIVATE' } }, { status: 409 }),
  );
  vi.stubGlobal('fetch', fetcher);
  await expect(run()).rejects.toThrow('Refresh saved records');
  await run().catch((e) => expect(e.message).not.toContain('PRIVATE'));
  expect(fetcher).toHaveBeenCalledTimes(2); // exactly one request per explicit invocation
});

test.each(['network', 'html', 'bad-json'])('unavailable/invalid response: %s', async (kind) => {
  const fetcher = vi.fn(async () => {
    if (kind === 'network') throw new Error('PRIVATE');
    return new Response(kind === 'html' ? '<html>PRIVATE</html>' : '{PRIVATE', {
      headers: { 'Content-Type': kind === 'html' ? 'text/html' : 'application/json' },
    });
  });
  vi.stubGlobal('fetch', fetcher);
  await expect(run()).rejects.toThrow('No automatic retry');
  expect(fetcher).toHaveBeenCalledTimes(1);
});

test('invalid selection performs no request', async () => {
  const fetcher = vi.fn();
  vi.stubGlobal('fetch', fetcher);
  await expect(
    compareNativeSkills(
      { ...profileMetadata(), profile_id: '/PRIVATE' },
      opportunityMetadata(),
      new AbortController().signal,
    ),
  ).rejects.toThrow('Select saved');
  await expect(
    compareNativeSkills(
      profileMetadata(),
      { ...opportunityMetadata(), revision: 0 },
      new AbortController().signal,
    ),
  ).rejects.toThrow('Select saved');
  expect(fetcher).not.toHaveBeenCalled();
});
