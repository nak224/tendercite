import { afterEach, expect, test, vi } from 'vitest';
import {
  addPipelineEntry,
  getPipelineEntry,
  listPipeline,
  updatePipelineEntry,
  pipelineInput,
  defaultPipelineValues,
} from './pipeline';
import { pipelineEntry, pipelineId } from '../test/pipeline';

afterEach(() => vi.unstubAllGlobals());
const signal = () => new AbortController().signal;
test('create uses fixed local resource and identities only; update sends full values and expected revision', async () => {
  const entry = pipelineEntry();
  const next = structuredClone(entry);
  next.record.revision = 2;
  next.record.values.stage = 'SUBMITTED';
  const fetcher = vi.fn(async (request: Request) => {
    expect(request.cache).toBe('no-store');
    expect(request.credentials).toBe('omit');
    expect(request.redirect).toBe('error');
    expect(new URL(request.url).origin).toBe(window.location.origin);
    if (request.method === 'POST') {
      expect(new URL(request.url).pathname).toBe('/tendercite-api/api/v1/pipeline');
      expect(await request.json()).toEqual({
        schema_version: 'native-pipeline-create-1',
        pipeline_id: pipelineId,
        opportunity_id: entry.record.opportunity_id,
      });
      return Response.json({
        schema_version: 'native-pipeline-save-result-1',
        status: 'CREATED',
        entry,
      });
    }
    expect(request.method).toBe('PUT');
    expect(await request.json()).toEqual({
      schema_version: 'native-pipeline-update-1',
      expected_revision: 1,
      values: next.record.values,
    });
    return Response.json({
      schema_version: 'native-pipeline-save-result-1',
      status: 'UPDATED',
      entry: next,
    });
  });
  vi.stubGlobal('fetch', fetcher);
  expect(await addPipelineEntry(pipelineId, entry.record.opportunity_id)).toEqual({
    status: 'CREATED',
    entry,
  });
  expect(await updatePipelineEntry(entry, next.record.values)).toEqual(next);
  expect(fetcher).toHaveBeenCalledTimes(2);
});
test('already tracked may return the existing identity and current revision, without resetting values', async () => {
  const entry = pipelineEntry();
  entry.record.revision = 4;
  entry.record.values.stage = 'WON';
  vi.stubGlobal(
    'fetch',
    vi.fn(async () =>
      Response.json({
        schema_version: 'native-pipeline-save-result-1',
        status: 'ALREADY_PRESENT',
        entry,
      }),
    ),
  );
  const result = await addPipelineEntry(
    '40000000-0000-0000-0000-000000000002',
    entry.record.opportunity_id,
  );
  expect(result.entry).toEqual(entry);
});
test('list applies exact stage/opportunity filters, pagination and response projection', async () => {
  const entry = pipelineEntry();
  vi.stubGlobal(
    'fetch',
    vi.fn(async (request: Request) => {
      expect(request.method).toBe('GET');
      const url = new URL(request.url);
      expect(url.searchParams.get('stage')).toBe('SAVED');
      expect(url.searchParams.get('opportunity_id')).toBe(entry.record.opportunity_id);
      expect(url.searchParams.get('offset')).toBe('50');
      return Response.json({
        schema_version: 'native-pipeline-listing-1',
        entries: [{ ...entry, PRIVATE: 'PRIVATE' }],
        next_offset: null,
      });
    }),
  );
  expect((await listPipeline(50, 'SAVED', signal(), entry.record.opportunity_id)).entries).toEqual([
    entry,
  ]);
});
test.each([
  'schema',
  'identity',
  'opportunity',
  'revision',
  'dates',
  'stage',
  'priority',
  'notes',
  'due-date',
])('rejects invalid entry %s', async (kind) => {
  const entry = pipelineEntry();
  const raw = entry as unknown as Record<string, unknown>;
  if (kind === 'schema') raw.schema_version = 'PRIVATE';
  if (kind === 'identity') entry.record.pipeline_id = entry.record.opportunity_id;
  if (kind === 'opportunity') entry.opportunity.opportunity_id = pipelineId;
  if (kind === 'revision') entry.record.revision = 0;
  if (kind === 'dates') entry.record.updated_at = '2020-01-01T00:00:00Z';
  if (kind === 'stage') (entry.record.values as unknown as Record<string, unknown>).stage = 'AUTO';
  if (kind === 'priority')
    (entry.record.values as unknown as Record<string, unknown>).priority = 'URGENT';
  if (kind === 'notes') entry.record.values.notes = 'x'.repeat(4001);
  if (kind === 'due-date') entry.record.values.next_action_due_date = '2027-02-30';
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => Response.json(entry)),
  );
  await expect(getPipelineEntry(pipelineId, signal())).rejects.toThrow('Invalid or mismatched');
});
test.each(['duplicate', 'wrong-filter', 'wrong-opportunity', 'offset'])(
  'rejects inconsistent listing %s',
  async (kind) => {
    const entry = pipelineEntry();
    const body = {
      schema_version: 'native-pipeline-listing-1',
      entries: [entry],
      next_offset: null as number | null,
    };
    if (kind === 'duplicate') body.entries.push(entry);
    if (kind === 'wrong-filter') entry.record.values.stage = 'WON';
    if (kind === 'wrong-opportunity') {
      entry.record.opportunity_id = pipelineId;
      entry.opportunity.opportunity_id = pipelineId;
    }
    if (kind === 'offset') body.next_offset = 1;
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => Response.json(body)),
    );
    await expect(
      listPipeline(0, 'SAVED', signal(), pipelineEntry().record.opportunity_id),
    ).rejects.toThrow('Invalid or mismatched');
  },
);
test.each(['status', 'revision', 'identity', 'values'])(
  'rejects mismatched update %s',
  async (kind) => {
    const entry = pipelineEntry(),
      next = pipelineEntry();
    next.record.revision = 2;
    if (kind === 'revision') next.record.revision = 3;
    if (kind === 'identity') next.record.pipeline_id = entry.record.opportunity_id;
    if (kind === 'values') next.record.values.notes = 'Changed';
    vi.stubGlobal(
      'fetch',
      vi.fn(async () =>
        Response.json({
          schema_version: 'native-pipeline-save-result-1',
          status: kind === 'status' ? 'CREATED' : 'UPDATED',
          entry: next,
        }),
      ),
    );
    await expect(updatePipelineEntry(entry, entry.record.values)).rejects.toThrow(
      'Invalid or mismatched',
    );
  },
);
test.each(['network', 'html', 'conflict', 'unknown-error', 'disabled'])(
  'safe %s error never retries',
  async (kind) => {
    const fetcher = vi.fn(async () => {
      if (kind === 'network') throw new Error('PRIVATE');
      if (kind === 'html')
        return new Response('<html>PRIVATE</html>', { headers: { 'Content-Type': 'text/html' } });
      return Response.json(
        {
          detail: {
            code:
              kind === 'conflict'
                ? 'PIPELINE_REVISION_CONFLICT'
                : kind === 'disabled'
                  ? 'PIPELINE_API_DISABLED'
                  : 'PRIVATE',
            message: 'PRIVATE',
          },
        },
        { status: 409 },
      );
    });
    vi.stubGlobal('fetch', fetcher);
    await updatePipelineEntry(pipelineEntry(), defaultPipelineValues()).catch((error) => {
      expect(error.message).not.toContain('PRIVATE');
      expect(error.message).toMatch(/Pipeline/);
    });
    expect(fetcher).toHaveBeenCalledTimes(1);
  },
);
test('invalid local input never requests a server path', async () => {
  const fetcher = vi.fn();
  vi.stubGlobal('fetch', fetcher);
  await expect(addPipelineEntry('/PRIVATE', pipelineId)).rejects.toThrow('Select an existing');
  await expect(listPipeline(-1, '', signal())).rejects.toThrow('Invalid pipeline');
  await expect(getPipelineEntry('https://PRIVATE', signal())).rejects.toThrow('Select an existing');
  expect(() =>
    pipelineInput({ ...defaultPipelineValues(), next_action_due_date: '0000-01-01' }),
  ).toThrow();
  expect(() => pipelineInput({ ...defaultPipelineValues(), notes: 'x'.repeat(4001) })).toThrow();
  expect(fetcher).not.toHaveBeenCalled();
});
