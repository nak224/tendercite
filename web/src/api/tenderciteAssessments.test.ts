import { afterEach, expect, test, vi } from 'vitest';
import {
  assessmentContext,
  assessmentRequest,
  assessmentStatuses,
  saveAssessment,
} from './tenderciteAssessments';
import { assessmentFindings, matrixRow } from '../test/assessments';

afterEach(() => vi.unstubAllGlobals());
test('assessment contract requires exact status and nonblank bounded rationale without altering text', () => {
  for (const status of assessmentStatuses)
    expect(assessmentRequest(status, '  Operator judgment.\n')).toEqual({
      status,
      rationale: '  Operator judgment.\n',
    });
  for (const rationale of ['', ' \n\t ', 'x'.repeat(4001)])
    expect(() => assessmentRequest('FULFILLED', rationale)).toThrow();
  expect(() => assessmentRequest('CONFIRMED', 'Rationale')).toThrow();
  expect(assessmentRequest('MISSING', 'x'.repeat(4000))).toBeTruthy();
});
test('PATCH sends only status/rationale through the fixed proxy with no retries', async () => {
  const request = assessmentRequest('PARTIAL', 'Synthetic judgment');
  const fetcher = vi.fn<(request: Request) => Promise<Response>>(async () =>
    Response.json(matrixRow(undefined, request)),
  );
  vi.stubGlobal('fetch', fetcher);
  await saveAssessment('finding-1', request);
  const sent = fetcher.mock.calls[0]![0];
  expect(sent.method).toBe('PATCH');
  expect(sent.url).toBe(`${window.location.origin}/tendercite-api/api/v1/go-no-go/finding-1`);
  expect(sent.credentials).toBe('omit');
  expect(sent.redirect).toBe('error');
  expect(await sent.json()).toEqual(request);
  expect(fetcher).toHaveBeenCalledTimes(1);
});
test.each(['identity', 'status', 'rationale', 'eligibility'])(
  'mismatched %s mutation confirmation is uncertain',
  async (kind) => {
    const input = assessmentRequest('FULFILLED', 'Synthetic rationale');
    const row: Record<string, unknown> = matrixRow(undefined, input) as unknown as Record<
      string,
      unknown
    >;
    if (kind === 'identity') row.finding_id = 'another-finding';
    if (kind === 'status') row.status = ['FULFILLED'];
    if (kind === 'rationale') row.rationale = null;
    if (kind === 'eligibility') row.review_status = 'REJECTED';
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => Response.json(row)),
    );
    await expect(saveAssessment('finding-1', input)).rejects.toThrow(
      /invalid assessment confirmation/,
    );
  },
);
test.each([
  'cross-analysis',
  'unknown-row',
  'duplicate-row',
  'missing-row',
  'changed-criterion',
  'changed-review',
  'wrong-evidence',
])('context rejects %s data without leaking source content', async (kind) => {
  const findings = structuredClone(assessmentFindings);
  const rows = [matrixRow(findings[0])];
  if (kind === 'cross-analysis') findings[0]!.analysis_run_id = 'other-analysis';
  if (kind === 'unknown-row') rows[0]!.finding_id = 'other-finding';
  if (kind === 'duplicate-row') rows.push(rows[0]!);
  if (kind === 'missing-row') rows.pop();
  if (kind === 'changed-criterion') rows[0]!.criterion = 'PRIVATE';
  if (kind === 'changed-review') rows[0]!.review_status = 'MODIFIED';
  if (kind === 'wrong-evidence') rows[0]!.evidence = [];
  vi.stubGlobal(
    'fetch',
    vi.fn(async (request: Request) =>
      Response.json(request.url.includes('/go-no-go') ? rows : findings),
    ),
  );
  await expect(assessmentContext('analysis-1', new AbortController().signal)).rejects.toThrow(
    /invalid assessment data|changed or is inconsistent/,
  );
});
test('both GET endpoints use explicit analysis scope; empty analyses are valid', async () => {
  const fetcher = vi.fn(async (request: Request) => {
    expect(new URL(request.url).searchParams.get('analysis_run_id')).toBe('analysis:one');
    return Response.json([]);
  });
  vi.stubGlobal('fetch', fetcher);
  expect(await assessmentContext('analysis:one', new AbortController().signal)).toEqual({
    findings: [],
    rows: [],
  });
  expect(fetcher).toHaveBeenCalledTimes(2);
});
