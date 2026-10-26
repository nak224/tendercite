// @vitest-environment node
import { afterEach, expect, test, vi } from 'vitest';
import { uploadTenderPdf } from './tendercite';
import { syntheticPdf } from '../test/tenderPdf';

afterEach(() => vi.unstubAllGlobals());

test('PDF multipart bytes use the fixed proxy endpoint without credentials or redirects', async () => {
  vi.stubGlobal('window', { location: { origin: 'http://127.0.0.1:5173' } });
  const document = {
    id: 'existing-id',
    filename: 'original.pdf',
    sha256: 'a'.repeat(64),
    page_count: 1,
    chunk_count: 1,
    created_at: '2026-10-16',
  };
  const fetcher = vi.fn<(request: Request) => Promise<Response>>(async () =>
    Response.json(document),
  );
  vi.stubGlobal('fetch', fetcher);
  const file = syntheticPdf();
  expect(await uploadTenderPdf(file)).toEqual(document);
  expect(fetcher).toHaveBeenCalledTimes(1);
  const request = fetcher.mock.calls[0]![0];
  expect(request.url).toBe('http://127.0.0.1:5173/tendercite-api/api/v1/documents');
  expect(request.method).toBe('POST');
  expect(request.redirect).toBe('error');
  expect(request.credentials).toBe('omit');
  expect(request.headers.get('content-type')).toContain('multipart/form-data; boundary=');
  const form = await request.formData();
  expect([...form.keys()]).toEqual(['file']);
  const transmitted = form.get('file') as File;
  expect(transmitted.name).toBe(file.name);
  expect(transmitted.type).toBe('application/pdf');
  expect(await transmitted.arrayBuffer()).toEqual(await file.arrayBuffer());
});
