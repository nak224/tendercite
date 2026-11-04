import { afterEach, expect, test, vi } from 'vitest';
import { bkmsCollection, bkmsMultiResults } from '../test/bkms';
import { getBkmsCollection, removeBkmsDay, searchAllBkms, parseCollection } from './bkmsCatalogs';
import { loadBkmsDay } from './bkms';
import { bkmsCatalog } from '../test/bkms';

afterEach(() => vi.unstubAllGlobals());
function mock(value: unknown, status = 200) {
  const f = vi.fn<(request: Request) => Promise<Response>>(async () =>
    Response.json(value, { status }),
  );
  vi.stubGlobal('fetch', f);
  return f;
}
test('collection read and explicitly scoped all-day search use fixed routes', async () => {
  let f = mock(bkmsCollection());
  expect(await getBkmsCollection()).toEqual(bkmsCollection());
  expect(f.mock.calls[0]![0].url).toBe(
    window.location.origin + '/tendercite-api/api/v1/discovery/bkms/catalogs',
  );
  expect(f.mock.calls[0]![0].method).toBe('GET');
  f = mock(bkmsMultiResults());
  await searchAllBkms('d'.repeat(64), 'Software', 1, 10, 'ALL', false);
  expect(await f.mock.calls[0]![0].json()).toEqual({
    schema_version: 'bkms-search-input-2',
    collection_id: 'd'.repeat(64),
    keyword: 'Software',
    page: 1,
    limit: 10,
    notice_category: 'ALL',
    include_earlier_versions: false,
  });
});
test('replacement and removal require current daily identity, without automatic retries', async () => {
  let f = mock(bkmsCatalog());
  await loadBkmsDay('2026-10-30', 'a'.repeat(64));
  expect(await f.mock.calls[0]![0].json()).toEqual({
    schema_version: 'bkms-load-input-1',
    export_day: '2026-10-30',
    expected_catalog_id: 'a'.repeat(64),
  });
  f = mock(bkmsCollection(true));
  expect((await removeBkmsDay('2026-10-30', 'a'.repeat(64))).days).toEqual([]);
  expect(await f.mock.calls[0]![0].json()).toEqual({
    schema_version: 'bkms-remove-input-1',
    export_day: '2026-10-30',
    expected_catalog_id: 'a'.repeat(64),
  });
  f = mock({ detail: { code: 'BKMS_CATALOG_CONFLICT', message: 'PRIVATE' } }, 409);
  await expect(removeBkmsDay('2026-10-30', 'a'.repeat(64))).rejects.toThrow(
    'BKMS_CATALOG_CONFLICT',
  );
  expect(f).toHaveBeenCalledTimes(1);
});
test('collection changes and forged observation scope fail safely', async () => {
  mock({ ...bkmsMultiResults(), coverage: { ...bkmsCollection(), collection_id: 'f'.repeat(64) } });
  await expect(searchAllBkms('d'.repeat(64), 'Software', 1, 10, 'ALL', false)).rejects.toThrow(
    'mismatched',
  );
  const bad = bkmsMultiResults();
  bad.items[0]!.occurrences[0]!.catalog_id = 'f'.repeat(64);
  mock(bad);
  await expect(searchAllBkms('d'.repeat(64), 'Software', 1, 10, 'ALL', false)).rejects.toThrow(
    'occurrence',
  );
  expect(() =>
    parseCollection({
      ...bkmsCollection(),
      days: [...bkmsCollection().days, ...bkmsCollection().days],
    }),
  ).toThrow('ordering');
});
test('unknown notice filters and lost mutations never retry or leak raw errors', async () => {
  const f = vi.fn(async () => {
    throw new Error('PRIVATE');
  });
  vi.stubGlobal('fetch', f);
  await expect(removeBkmsDay('2026-10-30', 'a'.repeat(64))).rejects.toThrow('No automatic retry');
  expect(f).toHaveBeenCalledTimes(1);
  await expect(
    searchAllBkms('d'.repeat(64), 'Software', 1, 10, 'OPEN' as never, false),
  ).rejects.toThrow('supported local filters');
  expect(f).toHaveBeenCalledTimes(1);
});
