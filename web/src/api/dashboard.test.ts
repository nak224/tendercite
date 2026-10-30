import { afterEach, expect, test, vi } from 'vitest';
import { dashboardPipeline } from './dashboard';
import { pipelineEntry } from '../test/pipeline';

afterEach(() => vi.unstubAllGlobals());
test('one bounded existing read projects only dashboard metadata/action fields', async () => {
  const entry = pipelineEntry();
  entry.record.values.notes = 'PRIVATE unrelated notes';
  entry.record.values.next_action = 'Synthetic next step';
  const fetcher = vi.fn(async (request: Request) => {
    expect(request.method).toBe('GET');
    expect(request.credentials).toBe('omit');
    expect(request.cache).toBe('no-store');
    expect(request.url).toBe(
      `${window.location.origin}/tendercite-api/api/v1/pipeline?limit=50&offset=0`,
    );
    return Response.json({
      schema_version: 'native-pipeline-listing-1',
      entries: [entry],
      next_offset: null,
    });
  });
  vi.stubGlobal('fetch', fetcher);
  const result = await dashboardPipeline(new AbortController().signal);
  expect(result.entries[0]).toEqual({
    pipeline_id: entry.record.pipeline_id,
    opportunity_id: entry.record.opportunity_id,
    title: entry.opportunity.title,
    stage: 'SAVED',
    priority: 'MEDIUM',
    next_action: 'Synthetic next step',
    next_action_due_date: null,
  });
  expect(JSON.stringify(result)).not.toContain('PRIVATE');
  expect(fetcher).toHaveBeenCalledTimes(1);
});
