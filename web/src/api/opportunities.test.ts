import { afterEach, expect, test, vi } from 'vitest';
import {
  getOpportunity,
  listOpportunities,
  opportunityInput,
  saveOpportunity,
  sourceUrlValid,
  emptyOpportunity,
} from './opportunities';
import { opportunityId, opportunityRecord } from '../test/opportunities';

afterEach(() => vi.unstubAllGlobals());
const signal = () => new AbortController().signal;
const mock = (reply: (request: Request) => Response | Promise<Response>) => {
  const fetcher = vi.fn(async (request: Request) => reply(request));
  vi.stubGlobal('fetch', fetcher);
  return fetcher;
};
test('create and update use the fixed local API and exact revision-checked contracts', async () => {
  const input = opportunityRecord().opportunity;
  const fetcher = mock(async (request) => {
    expect(new URL(request.url).origin).toBe(window.location.origin);
    expect(request.redirect).toBe('error');
    expect(request.credentials).toBe('omit');
    expect(request.cache).toBe('no-store');
    const update = request.method === 'PUT';
    expect(new URL(request.url).pathname).toBe(
      `/tendercite-api/api/v1/opportunities${update ? '/' + opportunityId : ''}`,
    );
    expect(await request.json()).toEqual(
      update
        ? {
            schema_version: 'native-opportunity-update-1',
            expected_revision: 1,
            opportunity: input,
          }
        : {
            schema_version: 'native-opportunity-create-1',
            opportunity_id: opportunityId,
            opportunity: input,
          },
    );
    return Response.json({
      schema_version: 'native-opportunity-save-result-1',
      status: update ? 'UPDATED' : 'CREATED',
      record: { ...opportunityRecord(), revision: update ? 2 : 1 },
    });
  });
  expect((await saveOpportunity(opportunityId, input)).status).toBe('CREATED');
  expect((await saveOpportunity(opportunityId, input, 1)).record.revision).toBe(2);
  expect(fetcher).toHaveBeenCalledTimes(2);
});
test('idempotent creation accepts ALREADY_PRESENT without automatic retries', async () => {
  const fetcher = mock(() =>
    Response.json({
      schema_version: 'native-opportunity-save-result-1',
      status: 'ALREADY_PRESENT',
      record: opportunityRecord(),
    }),
  );
  expect((await saveOpportunity(opportunityId, opportunityRecord().opportunity)).status).toBe(
    'ALREADY_PRESENT',
  );
  expect(fetcher).toHaveBeenCalledTimes(1);
});
test.each([
  'javascript:alert(1)',
  'file:///tmp/a',
  'ftp://example.invalid/a',
  'data:text/html,x',
  '//example.invalid/a',
  'https://',
  'https://user:secret@example.invalid/a',
  'https://@example.invalid',
  'https://example.invalid:0/a',
  'https://exa mple.invalid/a',
  'https://example.invalid\\@other.invalid/a',
])('unsupported URL %s is rejected before saving', async (url) => {
  const fetcher = mock(() => Response.json({}));
  expect(sourceUrlValid(url)).toBe(false);
  await expect(
    saveOpportunity(opportunityId, { ...opportunityRecord().opportunity, source_url: url }),
  ).rejects.toThrow(/HTTP\/HTTPS/);
  expect(fetcher).not.toHaveBeenCalled();
});
test.each(['', 'http://example.invalid/project', 'https://example.invalid/a?q=1#details'])(
  'URL reference %s is syntax-only and preserved',
  (url) => {
    expect(sourceUrlValid(url)).toBe(true);
    expect(
      opportunityInput({ ...opportunityRecord().opportunity, source_url: url }).source_url,
    ).toBe(url);
  },
);
test.each(['title', 'type', 'duplicate', 'budget', 'deadline', 'year-zero', 'verified'] as const)(
  'invalid %s declarations do not send a request',
  async (kind) => {
    const input = opportunityRecord().opportunity;
    if (kind === 'title') input.title = ' ';
    if (kind === 'type') Object.assign(input, { opportunity_type: 'PRIVATE' });
    if (kind === 'duplicate') input.required_skills = ['Python', ' ＰＹＴＨＯＮ '];
    if (kind === 'budget') input.required_skills = Array.from({ length: 101 }, (_, i) => String(i));
    if (kind === 'deadline') input.deadline = '2027-02-29';
    if (kind === 'year-zero') input.deadline = '0000-01-01';
    if (kind === 'verified') Object.assign(input, { verification_status: 'VERIFIED' });
    const fetcher = mock(() => Response.json({}));
    await expect(saveOpportunity(opportunityId, input)).rejects.toThrow(/Enter a title/);
    expect(fetcher).not.toHaveBeenCalled();
  },
);
test.each(['schema', 'identity', 'revision', 'content', 'source', 'verification'])(
  'mismatched %s response never confirms a save',
  async (kind) => {
    const record = opportunityRecord();
    if (kind === 'schema') Object.assign(record, { schema_version: 'PRIVATE' });
    if (kind === 'identity') record.opportunity_id = crypto.randomUUID();
    if (kind === 'revision') record.revision = 2;
    if (kind === 'content') record.opportunity.title = 'Other';
    if (kind === 'source') record.opportunity.source_url = 'javascript:PRIVATE';
    if (kind === 'verification')
      Object.assign(record.opportunity, { verification_status: 'VERIFIED' });
    mock(() =>
      Response.json({
        schema_version: 'native-opportunity-save-result-1',
        status: 'CREATED',
        record,
      }),
    );
    await expect(saveOpportunity(opportunityId, opportunityRecord().opportunity)).rejects.toThrow(
      /Invalid or mismatched/,
    );
  },
);
test.each([
  'OPPORTUNITIES_API_DISABLED',
  'OPPORTUNITY_REVISION_CONFLICT',
  'OPPORTUNITY_ID_CONFLICT',
  'PRIVATE',
])('safe fixed errors (%s) do not leak response bodies', async (code) => {
  const fetcher = mock(() =>
    Response.json({ detail: { code, message: 'PRIVATE' } }, { status: 409 }),
  );
  await expect(saveOpportunity(opportunityId, opportunityRecord().opportunity)).rejects.toThrow(
    `Opportunity request failed (HTTP 409)${code === 'PRIVATE' ? '' : ': ' + code}.`,
  );
  expect(fetcher).toHaveBeenCalledTimes(1);
});
test.each(['network', 'html', 'malformed'])(
  '%s failure never retries or exposes private text',
  async (kind) => {
    const fetcher = mock(() => {
      if (kind === 'network') throw Error('PRIVATE');
      return new Response('PRIVATE', {
        headers: { 'Content-Type': kind === 'html' ? 'text/html' : 'application/json' },
      });
    });
    await expect(saveOpportunity(opportunityId, opportunityRecord().opportunity)).rejects.toThrow(
      /response unavailable or invalid/,
    );
    expect(fetcher).toHaveBeenCalledTimes(1);
  },
);
test('listing and retrieval check identities, versions, dates and discard extra content', async () => {
  const record = opportunityRecord();
  const fetcher = mock((request) =>
    Response.json(
      request.url.includes('?')
        ? {
            schema_version: 'native-opportunity-listing-1',
            next_offset: null,
            opportunities: [
              {
                opportunity_id: record.opportunity_id,
                revision: 1,
                title: record.opportunity.title,
                opportunity_type: record.opportunity.opportunity_type,
                organization: record.opportunity.organization,
                deadline: record.opportunity.deadline,
                updated_at: record.updated_at,
                notes: 'PRIVATE',
              },
            ],
          }
        : { ...record, notes: 'PRIVATE' },
    ),
  );
  expect(JSON.stringify(await listOpportunities(0, signal()))).not.toContain('PRIVATE');
  expect(fetcher.mock.calls[0]![0].url).toMatch(/opportunities\?limit=50&offset=0$/);
  expect(await getOpportunity(opportunityId, signal())).toEqual(record);
  mock(() => Response.json({ ...record, opportunity_id: crypto.randomUUID() }));
  await expect(getOpportunity(opportunityId, signal())).rejects.toThrow(
    /mismatched opportunity response/,
  );
});
test('optional fields and empty skill collections need no related records', () => {
  expect(
    opportunityInput({ ...emptyOpportunity(), title: 'Synthetic other', opportunity_type: 'OTHER' })
      .required_skills,
  ).toEqual([]);
});
