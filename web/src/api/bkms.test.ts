import { afterEach, expect, test, vi } from 'vitest';
import { bkmsCatalog, bkmsResults, bkmsRecord, bkmsNotice, bkmsSource } from '../test/bkms';
import { currentBkmsCatalog, loadBkmsDay, searchBkms, importBkms } from './bkms';
import { bkmsSourceValid } from './bkmsReferences';
import { opportunityInput } from './opportunities';

afterEach(() => vi.unstubAllGlobals());
function mock(value: unknown, status = 200) {
  const fetcher = vi.fn(async () => Response.json(value, { status }));
  vi.stubGlobal('fetch', fetcher);
  return fetcher;
}
test('fixed-origin explicit load, local search, import and read contracts', async () => {
  let fetcher = mock(bkmsCatalog());
  expect(await currentBkmsCatalog()).toEqual(bkmsCatalog());
  expect(fetcher.mock.calls).toHaveLength(1);
  fetcher = mock(bkmsCatalog());
  await loadBkmsDay('2026-10-30');
  const sent = (fetcher.mock.calls[0] as unknown as [Request])[0];
  expect(sent.url).toBe(window.location.origin + '/tendercite-api/api/v1/discovery/bkms/load');
  expect(sent.method).toBe('POST');
  expect(sent.credentials).toBe('omit');
  expect(sent.redirect).toBe('error');
  expect(await sent.json()).toEqual({
    schema_version: 'bkms-load-input-1',
    export_day: '2026-10-30',
  });
  fetcher = mock(bkmsResults());
  expect((await searchBkms('a'.repeat(64), ' Software ', 1, 10)).items).toEqual([bkmsSource()]);
  expect(await (fetcher.mock.calls[0] as unknown as [Request])[0].json()).toEqual({
    schema_version: 'bkms-search-input-1',
    catalog_id: 'a'.repeat(64),
    keyword: 'Software',
    page: 1,
    limit: 10,
  });
  fetcher = mock({
    schema_version: 'native-opportunity-save-result-1',
    status: 'CREATED',
    record: bkmsRecord(),
  });
  expect((await importBkms('a'.repeat(64), bkmsNotice)).record).toEqual(bkmsRecord());
  expect(await (fetcher.mock.calls[0] as unknown as [Request])[0].json()).toEqual({
    schema_version: 'bkms-import-input-1',
    catalog_id: 'a'.repeat(64),
    external_id: bkmsNotice,
  });
});
test('catalog not found is an explicit empty state; disabled and private errors stay safe', async () => {
  mock({ detail: { code: 'BKMS_CATALOG_NOT_FOUND' } }, 404);
  expect(await currentBkmsCatalog()).toBeNull();
  mock({ detail: { code: 'PRIVATE', message: 'PRIVATE workforce records' } }, 500);
  await expect(loadBkmsDay('2026-10-30')).rejects.toThrow('HTTP 500');
  await expect(loadBkmsDay('2026-10-30')).rejects.not.toThrow('PRIVATE');
});
test.each(['', '*', '" OR query', 'https://example.invalid', 'x'.repeat(151)])(
  'invalid keyword %s never reaches provider',
  async (keyword) => {
    const fetcher = mock({});
    await expect(searchBkms('a'.repeat(64), keyword, 1, 10)).rejects.toThrow();
    expect(fetcher).not.toHaveBeenCalled();
  },
);
test.each([
  { external_id: '../PRIVATE' },
  { original_url: 'https://example.invalid' },
  { record_url: 'https://oeffentlichevergabe.de/api/notices/10000002?format=ocds&noticeVersion=1' },
  { license: 'PRIVATE' },
  { archive_sha256: 'invalid' },
  { original_metadata: { ...bkmsSource().original_metadata, tags: 'tender' } },
])('invalid source metadata rejected without import %j', (change) => {
  expect(bkmsSourceValid({ ...bkmsSource(), ...change })).toBe(false);
});
test('hash/day identity and malformed search responses fail safely', async () => {
  mock({ ...bkmsResults(), catalog: { ...bkmsCatalog(), catalog_id: 'f'.repeat(64) } });
  await expect(searchBkms('a'.repeat(64), 'Software', 1, 10)).rejects.toThrow('mismatched');
  mock({ ...bkmsResults(), items: [{ ...bkmsSource(), export_day: '2026-10-29' }] });
  await expect(searchBkms('a'.repeat(64), 'Software', 1, 10)).rejects.toThrow('mismatched');
  mock({ ...bkmsCatalog(), export_day: '2026-10-29' });
  await expect(loadBkmsDay('2026-10-30')).rejects.toThrow('Mismatched');
});
test('repeated import accepts current edited record; local editor preserves original provenance', async () => {
  const r = bkmsRecord();
  r.revision = 2;
  r.opportunity.title = 'My edit';
  r.opportunity.required_skills = ['Python'];
  mock({
    schema_version: 'native-opportunity-save-result-1',
    status: 'ALREADY_PRESENT',
    record: r,
  });
  expect((await importBkms('a'.repeat(64), bkmsNotice)).record).toEqual(r);
  expect(opportunityInput(r.opportunity).external_source).toEqual(bkmsSource());
  mock({ schema_version: 'native-opportunity-save-result-1', status: 'CREATED', record: r });
  await expect(importBkms('a'.repeat(64), bkmsNotice)).rejects.toThrow('uncertain');
});
test('lost mutation response has no automatic retry', async () => {
  const fetcher = vi.fn(async () => {
    throw new Error('PRIVATE');
  });
  vi.stubGlobal('fetch', fetcher);
  await expect(importBkms('a'.repeat(64), bkmsNotice)).rejects.toThrow('No automatic retry');
  expect(fetcher).toHaveBeenCalledTimes(1);
});
