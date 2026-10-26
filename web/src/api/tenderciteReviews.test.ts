import { afterEach, expect, test, vi } from 'vitest';
import {
  findingForReview,
  reviewHistory,
  reviewRequest,
  saveReview,
  type ReviewedValue,
} from './tenderciteReviews';
import { original, reviewEvent, reviewFinding } from '../test/reviews';

afterEach(() => vi.unstubAllGlobals());
test('review contracts use exact enums and limits without trimming or carrying hidden modifications', () => {
  const value = { ...original, statement: '  Explicit edited interpretation.\n' };
  expect(reviewRequest('MODIFY', value, ' Note ')).toEqual({
    action: 'MODIFY',
    reviewed_value: value,
    comment: ' Note ',
  });
  expect(reviewRequest('CONFIRM', value, '')).toEqual({ action: 'CONFIRM', comment: '' });
  expect(reviewRequest('REJECT', value, '')).toEqual({ action: 'REJECT', comment: '' });
  expect(
    reviewRequest('MODIFY', { ...value, statement: 'x'.repeat(4000) }, 'x'.repeat(4000)),
  ).toBeTruthy();
  for (const change of [
    { statement: ' ' },
    { statement: 'x'.repeat(4001) },
    { category: 'OTHER' },
    { requirement_type: 'INVALID' },
  ])
    expect(() => reviewRequest('MODIFY', { ...value, ...change } as ReviewedValue, '')).toThrow();
  expect(() => reviewRequest('CONFIRM', value, 'x'.repeat(4001))).toThrow();
});
test('review transport uses only fixed proxy, POST JSON, omitted credentials and redirect rejection', async () => {
  const request = reviewRequest('CONFIRM', original, 'Synthetic review');
  const fetcher = vi.fn<(request: Request) => Promise<Response>>(async () =>
    Response.json(reviewEvent(request), { status: 201 }),
  );
  vi.stubGlobal('fetch', fetcher);
  await saveReview('finding-1', request);
  const sent = fetcher.mock.calls[0]?.[0] as Request | undefined;
  expect(sent?.url).toBe(
    `${window.location.origin}/tendercite-api/api/v1/findings/finding-1/reviews`,
  );
  expect(sent?.method).toBe('POST');
  expect(sent?.credentials).toBe('omit');
  expect(sent?.redirect).toBe('error');
  expect(await sent?.json()).toEqual(request);
  expect(fetcher).toHaveBeenCalledTimes(1);
});
test.each(['finding', 'action', 'comment', 'value', 'status', 'date'])(
  'invalid %s review confirmation cannot be claimed saved',
  async (kind) => {
    const request = reviewRequest('MODIFY', { ...original, statement: 'Edited' }, 'Note');
    const event: Record<string, unknown> = { ...reviewEvent(request) };
    if (kind === 'finding') event.finding_id = 'other';
    if (kind === 'action') event.action = ['MODIFY'];
    if (kind === 'comment') event.comment = 'Other';
    if (kind === 'value') event.reviewed_value = original;
    if (kind === 'status') event.previous_status = 'FULFILLED';
    if (kind === 'date') event.created_at = null;
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => Response.json(event)),
    );
    await expect(saveReview('finding-1', request)).rejects.toThrow(/invalid review confirmation/);
  },
);
test('history and finding identities and history uniqueness are validated', async () => {
  const event = reviewEvent(reviewRequest('CONFIRM', original, ''));
  const fetcher = vi.fn(async () => Response.json([event, event]));
  vi.stubGlobal('fetch', fetcher);
  await expect(reviewHistory('finding-1', new AbortController().signal)).rejects.toThrow(
    /invalid review history/,
  );
  fetcher.mockImplementation(async () => Response.json({ ...reviewFinding, id: 'other' }));
  await expect(findingForReview('finding-1', new AbortController().signal)).rejects.toThrow(
    /invalid finding/,
  );
});
