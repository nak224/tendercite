import { afterEach, expect, test, vi } from 'vitest';
import { assessmentSelection, capabilityReport, reportFindings } from './capabilityReports';
import { inventoryFinding, otherHash } from '../test/mappings';
import { reportFixture } from '../test/reports';

afterEach(() => vi.unstubAllGlobals());
const signal = () => new AbortController().signal;
const selection = (ids = ['finding-1']) =>
  assessmentSelection('analysis-1', reportFixture().snapshot_hash, ids);
function mock(reply: (request: Request) => Response | Promise<Response>) {
  const fetcher = vi.fn(async (request: Request) => reply(request));
  vi.stubGlobal('fetch', fetcher);
  return fetcher;
}
test('exact versioned request uses one fixed POST, no credentials, redirects, cache or retries', async () => {
  const report = reportFixture(['a', 'z']);
  const fetcher = mock(async (request) => {
    expect(request.url).toBe(
      `${window.location.origin}/tendercite-api/api/v1/capability-workflow/report`,
    );
    expect(request.method).toBe('POST');
    expect(request.credentials).toBe('omit');
    expect(request.redirect).toBe('error');
    expect(request.cache).toBe('no-store');
    expect(request.headers.get('Content-Type')).toBe('application/json');
    expect(await request.json()).toEqual(selection(['a', 'z']));
    return Response.json(report);
  });
  expect(await capabilityReport(selection(['z', 'a']), signal())).toEqual(report);
  expect(fetcher).toHaveBeenCalledTimes(1);
});
test('empty and 100-finding selections preserve the existing limits and canonical ordering', async () => {
  for (const ids of [[], Array.from({ length: 100 }, (_, i) => `finding-${i}`)]) {
    const request = selection(ids);
    mock(() => Response.json(reportFixture(request.finding_ids)));
    expect((await capabilityReport(request, signal())).findings).toHaveLength(ids.length);
  }
});
test.each([
  ['finding-1', 'finding-1'],
  Array.from({ length: 101 }, (_, i) => String(i)),
  [''],
  ['a b'],
])('invalid finding selection %s fails before any request', async (...ids) => {
  const fetcher = mock(() => Response.json(reportFixture()));
  expect(() => selection(ids as string[])).toThrow(/at most 100 unique findings/);
  expect(fetcher).not.toHaveBeenCalled();
});
test('invalid scope is rejected and request objects cannot smuggle extra fields', async () => {
  expect(() => assessmentSelection('', 'PRIVATE', [])).toThrow();
  const fetcher = mock(() => Response.json(reportFixture()));
  await capabilityReport(
    { ...selection(), database_path: 'PRIVATE' } as ReturnType<typeof selection>,
    signal(),
  );
  expect(await fetcher.mock.calls[0]![0].json()).not.toHaveProperty('database_path');
});
test('finding discovery checks exact analysis membership and discards source text not needed for selection', async () => {
  const value = { ...inventoryFinding(), id: 'finding-1' };
  const fetcher = mock(() => Response.json([value]));
  expect(await reportFindings('analysis-1', signal())).toEqual([
    {
      id: value.id,
      analysis_run_id: value.analysis_run_id,
      effective_value: value.effective_value,
      review_status: value.review_status,
    },
  ]);
  expect(fetcher.mock.calls[0]![0].url).toMatch(/findings\?analysis_run_id=analysis-1$/);
  mock(() => Response.json([inventoryFinding('foreign')]));
  await expect(reportFindings('analysis-1', signal())).rejects.toThrow(/mismatched finding/);
});
test.each([
  'schema',
  'model',
  'algorithm',
  'analysis',
  'snapshot',
  'source-hash',
  'missing-finding',
  'extra-finding',
  'duplicate',
  'qualification',
  'person',
  'coverage',
  'catalog-skill',
  'summary',
  'summary-counts',
  'individual-counts',
  'unapproved-coverage',
  'limitations',
])('rejects malformed or mismatched %s report without displaying private data', async (kind) => {
  const data = reportFixture();
  if (kind === 'schema') Object.assign(data, { schema_version: 'PRIVATE' });
  if (kind === 'model') Object.assign(data, { matching_contract_version: 'PRIVATE' });
  if (kind === 'algorithm') Object.assign(data, { algorithm_version: 'PRIVATE' });
  if (kind === 'analysis') data.analysis_id = 'other';
  if (kind === 'snapshot') data.snapshot_hash = data.snapshot.content_hash = otherHash;
  if (kind === 'source-hash') data.snapshot.content_hash = otherHash;
  if (kind === 'missing-finding') data.findings = [];
  if (kind === 'extra-finding') data.findings.push(reportFixture(['other']).findings[0]!);
  if (kind === 'duplicate') data.findings.push(data.findings[0]!);
  if (kind === 'qualification') Object.assign(data, { qualification: 'VERIFIED' });
  if (kind === 'person') data.findings[0]!.individual_coverage[0]!.person_id = 'PRIVATE';
  if (kind === 'coverage') Object.assign(data.findings[0]!, { coverage: 'PRIVATE' });
  if (kind === 'catalog-skill')
    data.findings[0]!.individual_coverage[0]!.missing_assignment_ids = [
      '00000000-0000-0000-0000-000000009999',
    ];
  if (kind === 'summary') data.summary.selected_finding_count = 0;
  if (kind === 'summary-counts') data.summary.current_approved = 0;
  if (kind === 'individual-counts') data.findings[0]!.individual_counts.all_required_recorded = 4;
  if (kind === 'unapproved-coverage') {
    data.findings[0]!.finding.mapping.status = 'DRAFT';
    data.findings[0]!.finding.mapping.approved_review_binding = null;
  }
  if (kind === 'limitations') Object.assign(data.findings[0]!, { limitations: [null] });
  mock(() => Response.json(data));
  await expect(capabilityReport(selection(), signal())).rejects.toThrow(
    'Invalid or mismatched capability report. No result displayed.',
  );
});
test.each([404, 409, 422, 503])(
  'safe HTTP %s errors expose only allowlisted codes',
  async (status) => {
    const fetcher = mock(() =>
      Response.json({ detail: { code: 'PRIVATE', message: 'PRIVATE' } }, { status }),
    );
    await expect(capabilityReport(selection(), signal())).rejects.toThrow(
      `Capability report failed (HTTP ${status}).`,
    );
    expect(fetcher).toHaveBeenCalledTimes(1);
  },
);
test('disabled report API is distinguished from unavailable snapshot discovery', async () => {
  mock(() =>
    Response.json({ detail: { code: 'CAPABILITY_REPORT_API_DISABLED' } }, { status: 404 }),
  );
  await expect(capabilityReport(selection(), signal())).rejects.toThrow(
    /CAPABILITY_REPORT_API_DISABLED/,
  );
});
test.each(['network', 'html', 'json'])(
  'safe %s failure does not retry or expose response content',
  async (kind) => {
    const fetcher = mock(() => {
      if (kind === 'network') throw Error('PRIVATE');
      return new Response('PRIVATE', {
        headers: { 'content-type': kind === 'html' ? 'text/html' : 'application/json' },
      });
    });
    await expect(capabilityReport(selection(), signal())).rejects.toThrow(/unavailable or invalid/);
    expect(fetcher).toHaveBeenCalledTimes(1);
  },
);
test('unexpected personnel fields are not retained by the report client', async () => {
  const data = reportFixture();
  Object.assign(data, { email: 'PRIVATE' });
  Object.assign(data.findings[0]!.individual_coverage[0]!, { name: 'PRIVATE', notes: 'PRIVATE' });
  mock(() => Response.json(data));
  expect(JSON.stringify(await capabilityReport(selection(), signal()))).not.toContain('PRIVATE');
});
