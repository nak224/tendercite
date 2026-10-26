import { afterEach, expect, test, vi } from 'vitest';
import { MAX_SNAPSHOT_BYTES, readSnapshotFile, sendSnapshot } from './tenderciteSnapshots';
import {
  snapshotFile,
  snapshotFileBytes,
  snapshotFileText,
  snapshotSummary,
} from '../test/snapshotImport';

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});
test.each(['empty', 'extension', 'mime', 'oversized'])(
  '%s file is rejected before reading or requesting',
  async (kind) => {
    const file = new File(
      [kind === 'empty' ? '' : '{}'],
      kind === 'extension' ? 'x.pdf' : 'x.json',
      {
        type: kind === 'mime' ? 'application/pdf' : 'application/json',
      },
    );
    if (kind === 'oversized')
      Object.defineProperty(file, 'size', { value: MAX_SNAPSHOT_BYTES + 1 });
    const read = vi.spyOn(FileReader.prototype, 'readAsArrayBuffer');
    const fetcher = vi.fn();
    vi.stubGlobal('fetch', fetcher);
    await expect(readSnapshotFile(file)).rejects.toThrow(/nonempty .json file.*16 MiB/);
    expect(read).not.toHaveBeenCalled();
    expect(fetcher).not.toHaveBeenCalled();
  },
);
test('exact 16 MiB file is accepted, and invalid byte buffers cannot be sent', async () => {
  const bytes = new Uint8Array(MAX_SNAPSHOT_BYTES);
  expect((await readSnapshotFile(new File([bytes], 'boundary.json'))).byteLength).toBe(
    MAX_SNAPSHOT_BYTES,
  );
  const fetcher = vi.fn();
  vi.stubGlobal('fetch', fetcher);
  for (const size of [0, MAX_SNAPSHOT_BYTES + 1])
    await expect(sendSnapshot('validate', new ArrayBuffer(size))).rejects.toThrow(/16 MiB/);
  expect(fetcher).not.toHaveBeenCalled();
});
test('read errors are sanitized', async () => {
  vi.spyOn(FileReader.prototype, 'readAsArrayBuffer').mockImplementation(function (
    this: FileReader,
  ) {
    this.dispatchEvent(new ProgressEvent('error'));
  });
  await expect(readSnapshotFile(snapshotFile())).rejects.toThrow(
    'The selected JSON file could not be read.',
  );
});
test('same raw bytes including whitespace reach both fixed endpoints without credentials or redirects', async () => {
  const requests: Request[] = [];
  vi.stubGlobal(
    'fetch',
    vi.fn(async (request: Request) => {
      requests.push(request);
      return Response.json(
        snapshotSummary(request.url.endsWith('/import') ? 'INSERTED' : 'VALIDATED'),
      );
    }),
  );
  const bytes = await readSnapshotFile(snapshotFile());
  expect(new Uint8Array(bytes)).toEqual(new Uint8Array(snapshotFileBytes()));
  const validated = await sendSnapshot('validate', bytes);
  expect(await sendSnapshot('import', bytes, validated, true)).toEqual(snapshotSummary('INSERTED'));
  for (const [index, request] of requests.entries()) {
    const operation = index === 0 ? 'validate' : 'import';
    expect(request.url).toBe(
      `${window.location.origin}/tendercite-api/api/v1/capability-snapshots/${operation}`,
    );
    expect(request.method).toBe('POST');
    expect(request.credentials).toBe('omit');
    expect(request.redirect).toBe('error');
    expect(request.cache).toBe('no-store');
    expect(request.headers.get('content-type')).toBe('application/json');
    expect(request.headers.get('X-TenderCite-Acknowledge-Local-Synthetic-Data')).toBe(
      index ? 'true' : null,
    );
    expect(await request.text()).toBe(snapshotFileText);
  }
});
test('duplicate keys are transmitted intact for the authoritative server to reject', async () => {
  let sent = '';
  vi.stubGlobal(
    'fetch',
    vi.fn(async (request: Request) => {
      sent = await request.text();
      return Response.json({ detail: { code: 'INVALID_JSON' } }, { status: 422 });
    }),
  );
  const text = '{"schema_version":"1.0", "schema_version":"1.0"}\n';
  await expect(sendSnapshot('validate', new TextEncoder().encode(text).buffer)).rejects.toThrow(
    'INVALID_JSON',
  );
  expect(sent).toBe(text);
});
test.each([false, true])(
  'import without validated identity/acknowledgement (%s) never requests',
  async (ack) => {
    const fetcher = vi.fn();
    vi.stubGlobal('fetch', fetcher);
    await expect(
      sendSnapshot('import', snapshotFileBytes(), ack ? undefined : snapshotSummary(), ack),
    ).rejects.toThrow(/Validate/);
    expect(fetcher).not.toHaveBeenCalled();
  },
);
test.each(['INSERTED', 'ALREADY_PRESENT'] as const)(
  '%s response is checked and projected to metadata only',
  async (status) => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => Response.json({ ...snapshotSummary(status), private: 'PRIVATE' })),
    );
    expect(await sendSnapshot('import', snapshotFileBytes(), snapshotSummary(), true)).toEqual(
      snapshotSummary(status),
    );
  },
);
test.each([
  'hash',
  'start',
  'end',
  'members',
  'assignments',
  'catalog',
  'schema',
  'source',
  'timestamp',
  'status',
])('mismatched import %s cannot confirm persistence', async (kind) => {
  const data: Record<string, unknown> = snapshotSummary('INSERTED') as unknown as Record<
    string,
    unknown
  >;
  if (kind === 'hash') data.content_hash = `sha256:${'a'.repeat(64)}`;
  if (kind === 'start')
    data.assessment_period = { ...snapshotSummary().assessment_period, start_date: '2027-02-02' };
  if (kind === 'end')
    data.assessment_period = { ...snapshotSummary().assessment_period, end_date: '2027-02-08' };
  if (kind === 'members') data.member_count = 4;
  if (kind === 'assignments') data.assignment_count = 4;
  if (kind === 'catalog') data.catalog_count = 4;
  if (kind === 'schema') data.schema_version = '2.0';
  if (kind === 'source') data.source_system = 'other';
  if (kind === 'timestamp') data.imported_at = null;
  if (kind === 'status') data.status = ['INSERTED'];
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => Response.json(data)),
  );
  await expect(
    sendSnapshot('import', snapshotFileBytes(), snapshotSummary(), true),
  ).rejects.toThrow(/invalid or mismatched snapshot summary/);
});
test.each([
  null,
  [],
  { ...snapshotSummary(), status: 'INSERTED' },
  { ...snapshotSummary(), content_hash: 'PRIVATE' },
  { ...snapshotSummary(), member_count: -1 },
  { ...snapshotSummary(), assignment_count: 1.5 },
  { ...snapshotSummary(), catalog_count: '2' },
  { ...snapshotSummary(), assessment_period: { start_date: '2027-02-30', end_date: '2027-03-01' } },
  { ...snapshotSummary(), imported_at: '2026-10-24T12:00:00Z' },
])('invalid validation metadata never authorizes import: %#', async (data) => {
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => Response.json(data)),
  );
  await expect(sendSnapshot('validate', snapshotFileBytes())).rejects.toThrow(
    /invalid or mismatched/,
  );
});
test.each([
  'HASH_MISMATCH',
  'UNSUPPORTED_SCHEMA',
  'INVALID_CONTRACT',
  'CAPABILITY_SNAPSHOT_API_DISABLED',
  'PRIVATE',
])('HTTP error %s uses only allowlisted codes without retries', async (code) => {
  const fetcher = vi.fn(async () =>
    Response.json({ detail: { code, message: 'PRIVATE' } }, { status: 422 }),
  );
  vi.stubGlobal('fetch', fetcher);
  const message =
    code === 'PRIVATE'
      ? 'Snapshot request failed (HTTP 422).'
      : `Snapshot request failed (HTTP 422): ${code}.`;
  await expect(sendSnapshot('validate', snapshotFileBytes())).rejects.toThrow(message);
  expect(fetcher).toHaveBeenCalledTimes(1);
});
test.each(['network', 'HTML', 'JSON'] as const)(
  '%s response failure is sanitized and never retried',
  async (kind) => {
    const fetcher = vi.fn(async () => {
      if (kind === 'network') throw Error('PRIVATE');
      return new Response('PRIVATE', {
        headers: { 'content-type': kind === 'HTML' ? 'text/html' : 'application/json' },
      });
    });
    vi.stubGlobal('fetch', fetcher);
    await expect(
      sendSnapshot('import', snapshotFileBytes(), snapshotSummary(), true),
    ).rejects.toThrow('TenderCite snapshot response unavailable or invalid.');
    expect(fetcher).toHaveBeenCalledTimes(1);
  },
);
