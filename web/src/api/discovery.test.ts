import { afterEach, expect, test, vi } from 'vitest';
import { importTed, searchTed } from './discovery';
import { saveOpportunity } from './opportunities';
import { tedId, tedRecord, tedResults } from '../test/discovery';

afterEach(() => vi.unstubAllGlobals());
const run = () => searchTed('Software', 1, 10, new AbortController().signal);
test('fixed local search/import operations have explicit bounded payloads and safe transport', async () => {
  const fetcher = vi.fn(async (request: Request) => {
    expect(request.method).toBe('POST');
    expect(request.credentials).toBe('omit');
    expect(request.cache).toBe('no-store');
    expect(request.redirect).toBe('error');
    expect(new URL(request.url).origin).toBe(window.location.origin);
    const body = await request.json();
    if (request.url.endsWith('/search')) {
      expect(body).toEqual({
        schema_version: 'ted-search-input-1',
        keyword: 'Software',
        page: 1,
        limit: 10,
      });
      return Response.json(tedResults());
    }
    expect(body).toEqual({ schema_version: 'ted-import-input-1', publication_id: tedId });
    return Response.json({
      schema_version: 'native-opportunity-save-result-1',
      status: 'CREATED',
      record: tedRecord(),
    });
  });
  vi.stubGlobal('fetch', fetcher);
  expect(await run()).toEqual(tedResults());
  expect((await importTed(tedId)).record).toEqual(tedRecord());
  expect(fetcher).toHaveBeenCalledTimes(2);
});
test.each([
  'source',
  'url',
  'identity',
  'page',
  'keyword',
  'count',
  'title',
  'importable',
  'warning',
  'duplicate',
  'deadline',
  'summary',
])('rejects malformed discovery result %s', async (kind) => {
  const result = tedResults(),
    item = result.items[0]!;
  if (kind === 'source') (result as unknown as Record<string, unknown>).source = 'PRIVATE';
  if (kind === 'url') item.source_url = 'https://example.invalid/PRIVATE';
  if (kind === 'identity') item.publication_id = 'PRIVATE';
  if (kind === 'page') result.page = 2;
  if (kind === 'keyword') result.keyword = 'PRIVATE';
  if (kind === 'count') result.total_notice_count = 0;
  if (kind === 'title') item.title = 'x'.repeat(201);
  if (kind === 'importable') item.importable = false;
  if (kind === 'warning') item.warnings = ['PRIVATE'];
  if (kind === 'duplicate') result.items.push(item);
  if (kind === 'deadline') item.submission_deadline = '2099-02-30';
  if (kind === 'summary') item.summary = 'x'.repeat(2001);
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => Response.json(result)),
  );
  await expect(run()).rejects.toThrow('Invalid or mismatched');
});
test('empty and later pages preserve their requested scope', async () => {
  const result = tedResults();
  result.items = [];
  result.total_notice_count = 0;
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => Response.json(result)),
  );
  expect((await run()).items).toEqual([]);
  result.page = 2;
  result.total_notice_count = 50;
  result.next_page = 3;
  expect((await searchTed('Software', 2, 10, new AbortController().signal)).next_page).toBe(3);
});
test.each(['origin', 'source-id', 'source-url', 'qualifier', 'revision', 'skills'])(
  'import result must have truthful source binding: %s',
  async (kind) => {
    const record = tedRecord();
    if (kind === 'origin') {
      record.opportunity.source_origin = 'manual_operator_entry';
      record.opportunity.external_source = null;
    }
    if (kind === 'source-id') record.opportunity.external_source!.external_id = '99999992-2099';
    if (kind === 'source-url')
      record.opportunity.external_source!.original_url = 'https://example.invalid/PRIVATE';
    if (kind === 'qualifier')
      (record.opportunity as unknown as Record<string, unknown>).verification_status = 'VERIFIED';
    if (kind === 'revision') record.revision = 2;
    if (kind === 'skills') record.opportunity.required_skills = ['Python'];
    vi.stubGlobal(
      'fetch',
      vi.fn(async () =>
        Response.json({
          schema_version: 'native-opportunity-save-result-1',
          status: 'CREATED',
          record,
        }),
      ),
    );
    await expect(importTed(tedId)).rejects.toThrow(/uncertain/);
  },
);
test('duplicate imports return user-edited current records and preserve provenance on explicit updates', async () => {
  const record = tedRecord();
  record.revision = 3;
  record.opportunity.title = 'My local title';
  record.opportunity.required_skills = ['Python'];
  const fetcher = vi.fn(async (request: Request) => {
    if (request.method === 'POST')
      return Response.json({
        schema_version: 'native-opportunity-save-result-1',
        status: 'ALREADY_PRESENT',
        record,
      });
    const body = await request.json();
    expect(body.expected_revision).toBe(3);
    expect(body.opportunity.external_source).toEqual(record.opportunity.external_source);
    return Response.json({
      schema_version: 'native-opportunity-save-result-1',
      status: 'UPDATED',
      record: { ...record, revision: 4 },
    });
  });
  vi.stubGlobal('fetch', fetcher);
  expect((await importTed(tedId)).record).toEqual(record);
  expect(
    (await saveOpportunity(record.opportunity_id, record.opportunity, 3)).record.revision,
  ).toBe(4);
});
test.each(['network', 'html', 'rate', 'disabled', 'unknown'])(
  'safe %s errors never retry',
  async (kind) => {
    const fetcher = vi.fn(async () => {
      if (kind === 'network') throw new Error('PRIVATE');
      if (kind === 'html')
        return new Response('<html>PRIVATE</html>', { headers: { 'Content-Type': 'text/html' } });
      return Response.json(
        {
          detail: {
            code:
              kind === 'rate'
                ? 'TED_RATE_LIMITED'
                : kind === 'disabled'
                  ? 'DISCOVERY_API_DISABLED'
                  : 'PRIVATE',
            message: 'PRIVATE',
          },
        },
        { status: 429 },
      );
    });
    vi.stubGlobal('fetch', fetcher);
    await run().catch((e) => expect(e.message).not.toContain('PRIVATE'));
    expect(fetcher).toHaveBeenCalledTimes(1);
  },
);
test('invalid requests perform no network operations', async () => {
  const fetcher = vi.fn();
  vi.stubGlobal('fetch', fetcher);
  await expect(searchTed('x" OR 1=1', 1, 10, new AbortController().signal)).rejects.toThrow(
    'literal',
  );
  await expect(importTed('https://PRIVATE')).rejects.toThrow('Select');
  expect(fetcher).not.toHaveBeenCalled();
});
