import { afterEach, expect, test, vi } from 'vitest';
import { bkmsCatalog, bkmsMultiResults, bkmsSource } from '../test/bkms';
import { deadlineEvidence, deadlineSummary } from '../test/bkmsDeadlines';
import { readDeadlineSummary, loadDeadlines, parseDeadlineEvidence } from './bkmsDeadlines';
import { searchAllBkms } from './bkmsCatalogs';
afterEach(() => vi.unstubAllGlobals());
function mock(value: unknown, code = 200) {
  const f = vi.fn<(r: Request) => Promise<Response>>(async () =>
    Response.json(value, { status: code }),
  );
  vi.stubGlobal('fetch', f);
  return f;
}
test('bounded fixed-origin read and explicit exact-scope download with current replacement token', async () => {
  let f = mock(deadlineSummary(false));
  expect((await readDeadlineSummary(bkmsCatalog())).state).toBe('NOT_LOADED');
  expect(f.mock.calls[0]![0].url).toBe(
    window.location.origin + '/tendercite-api/api/v1/discovery/bkms/deadlines/' + 'a'.repeat(64),
  );
  expect(f.mock.calls[0]![0].method).toBe('GET');
  f = mock(deadlineSummary());
  await loadDeadlines(bkmsCatalog(), null);
  expect(await f.mock.calls[0]![0].json()).toEqual({
    schema_version: 'bkms-deadline-load-1',
    catalog_id: 'a'.repeat(64),
  });
  expect(f.mock.calls[0]![0].url).toContain('/discovery/bkms/deadlines/load');
  await loadDeadlines(bkmsCatalog(), '9'.repeat(64));
  expect((await f.mock.calls[1]![0].json()).expected_enrichment_id).toBe('9'.repeat(64));
});
test('exact identity and original offsets survive combined-search parsing; forged versions do not', async () => {
  const value = {
    ...bkmsMultiResults(),
    items: [{ ...bkmsMultiResults().items[0]!, deadline_enrichment: deadlineEvidence() }],
  };
  mock(value);
  const r = await searchAllBkms('d'.repeat(64), 'Software', 1, 10, 'ALL', false);
  expect(r.items[0]!.deadline_enrichment).toEqual(deadlineEvidence());
  const bad = deadlineEvidence();
  bad.notice.external_id = '10000001-01';
  expect(() => parseDeadlineEvidence(bad, bkmsSource(), 'a'.repeat(64))).toThrow('mismatched');
  expect(() =>
    parseDeadlineEvidence(
      { ...deadlineEvidence(), catalog_id: 'b'.repeat(64) },
      bkmsSource(),
      'a'.repeat(64),
    ),
  ).toThrow('mismatched');
  expect(() =>
    parseDeadlineEvidence(
      { ...deadlineEvidence(), export_day: '2026-10-29' },
      bkmsSource(),
      'a'.repeat(64),
    ),
  ).toThrow('mismatched');
});
test('unsafe summary/evidence shapes are rejected and unsupported schemas cannot claim success', async () => {
  mock({ ...deadlineSummary(), bound_catalog_id: 'b'.repeat(64) });
  await expect(readDeadlineSummary(bkmsCatalog())).rejects.toThrow('mismatched');
  const e = deadlineEvidence();
  e.notice.customization_id = 'unsupported';
  expect(() => parseDeadlineEvidence(e, bkmsSource(), 'a'.repeat(64))).toThrow('mismatched');
  const bad = deadlineEvidence();
  bad.notice.lots[0]!.periods[0]!.dates = ['x'.repeat(41)];
  expect(() => parseDeadlineEvidence(bad, bkmsSource(), 'a'.repeat(64))).toThrow('mismatched');
});
test('HTTP conflicts and lost responses never expose bodies or retry downloads', async () => {
  const f = mock({ detail: { code: 'BKMS_DEADLINE_CONFLICT', message: 'PRIVATE' } }, 409);
  await expect(loadDeadlines(bkmsCatalog(), null)).rejects.toThrow('BKMS_DEADLINE_CONFLICT');
  expect(f).toHaveBeenCalledTimes(1);
  const lost = vi.fn(async () => {
    throw new Error('PRIVATE');
  });
  vi.stubGlobal('fetch', lost);
  await expect(loadDeadlines(bkmsCatalog(), null)).rejects.toThrow('No automatic retry');
  expect(lost).toHaveBeenCalledTimes(1);
});
