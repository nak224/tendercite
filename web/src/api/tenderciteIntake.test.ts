import { afterEach, expect, test, vi } from 'vitest';
import { buildIntake, intakeBody, sendIntake, MAX_INTAKE_BYTES } from './tenderciteIntake';
import { draft, sources, quote, summary } from '../test/intake';
afterEach(() => vi.unstubAllGlobals());

test('payload preserves text and uses exact referenced identities with a deduplicated inventory', () => {
  const drafts = [draft('item-8', 'doc-two'), draft('item-2'), draft('item-9', 'doc-two')];
  const body = buildIntake('Synthetic purpose', drafts, sources);
  expect(body).toEqual({
    schema_version: 'curated-tender-intake-1',
    source_origin: 'manual_operator_entry',
    purpose: 'Synthetic purpose',
    documents: sources
      .slice(0, 2)
      .map((doc) => ({ document_id: doc.id, expected_pdf_sha256: doc.sha256 })),
    findings: drafts.map((row) => ({
      ...row,
      evidence: row.evidence.map((c) => ({ ...c, page_number: 2 })),
    })),
  });
  expect(body.findings[0]?.evidence[0]?.quote).toBe(quote);
  expect(body.findings.map((row) => row.item_id)).toEqual(['item-8', 'item-2', 'item-9']);
  expect(JSON.stringify(body)).not.toMatch(/confidence|review_status|assessment|snapshot/);
});
test('multiple citation arrays remain supported by the payload builder', () => {
  const row = draft();
  row.evidence.push({ ...row.evidence[0]!, document_id: 'doc-two', page_number: '1' });
  expect(buildIntake('Synthetic', [row], sources).documents).toHaveLength(2);
});
test.each(['source', 'hash', 'page', 'quote', 'category', 'type', 'statement', 'duplicate'])(
  'invalid %s selection cannot create an intake',
  (failure) => {
    const row = draft();
    const documents = structuredClone(sources);
    const rows = [row];
    if (failure === 'source') row.evidence[0]!.document_id = 'missing';
    if (failure === 'hash') documents[0]!.sha256 = 'invalid';
    if (failure === 'page') row.evidence[0]!.page_number = '4';
    if (failure === 'quote') row.evidence[0]!.quote = ' ';
    if (failure === 'category') row.category = '';
    if (failure === 'type') row.requirement_type = '';
    if (failure === 'statement') row.statement = '';
    if (failure === 'duplicate') rows.push(structuredClone(row));
    expect(() => buildIntake('Synthetic', rows, documents)).toThrow();
  },
);
test('empty / oversized requirement lists and missing purpose are rejected', () => {
  expect(() => buildIntake('Synthetic', [], sources)).toThrow();
  expect(() => buildIntake(' ', [draft()], sources)).toThrow();
  expect(() =>
    buildIntake(
      'Synthetic',
      Array.from({ length: 101 }, (_, i) => draft(`item-${i}`)),
      sources,
    ),
  ).toThrow();
});
test('one MiB is measured in UTF-8 bytes including the confirmation envelope', () => {
  const row = draft();
  row.statement = '😀'.repeat(2000);
  row.evidence[0]!.quote = row.statement;
  const rows = Array.from({ length: 100 }, (_, i) => ({
    ...structuredClone(row),
    item_id: `item-${i}`,
  }));
  expect(() => buildIntake('Synthetic', rows, sources)).toThrow(/1 MiB/);
  const body = buildIntake('Synthetic', [draft()], sources);
  expect(new TextEncoder().encode(intakeBody(body, 'commit')).byteLength).toBeLessThan(
    MAX_INTAKE_BYTES,
  );
  expect(JSON.parse(intakeBody(body, 'commit'))).toEqual({
    intake: body,
    confirm_manual_intake: true,
  });
});
test('commit without a validated summary is rejected without a request', async () => {
  const fetcher = vi.fn();
  vi.stubGlobal('fetch', fetcher);
  await expect(sendIntake('commit', buildIntake('Synthetic', [draft()], sources))).rejects.toThrow(
    /Validate/,
  );
  expect(fetcher).not.toHaveBeenCalled();
});
test('POST transport uses fixed origin and rejects a mismatched commit identity', async () => {
  const body = buildIntake('Synthetic', [draft()], sources);
  const validated = summary(body);
  const fetcher = vi.fn<(request: Request) => Promise<Response>>(async () =>
    Response.json({
      ...validated,
      status: 'CREATED',
      analysis_id: `curated-${'e'.repeat(64)}`,
      intake_content_hash: `sha256:${'e'.repeat(64)}`,
    }),
  );
  vi.stubGlobal('fetch', fetcher);
  await expect(sendIntake('commit', body, validated)).rejects.toThrow(/invalid intake summary/);
  const req = fetcher.mock.calls[0]?.[0] as Request | undefined;
  // Use the actual sent request, not a fabricated provider response, to check transport.
  expect(req?.url).toBe(`${window.location.origin}/tendercite-api/api/v1/curated-intakes/commit`);
  expect(req?.credentials).toBe('omit');
  expect(req?.redirect).toBe('error');
});
test.each(['schema', 'status', 'counts', 'identity'])(
  'invalid %s validation response never becomes a usable summary',
  async (failure) => {
    const body = buildIntake('Synthetic', [draft()], sources);
    const data: Record<string, unknown> = { ...summary(body) };
    if (failure === 'schema') data.schema_version = 'other';
    if (failure === 'status') data.status = 'CREATED';
    if (failure === 'counts') data.quotation_count = 0;
    if (failure === 'identity') data.analysis_id = 'https://external.invalid/PRIVATE';
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => Response.json(data)),
    );
    await expect(sendIntake('validate', body)).rejects.toThrow(/invalid intake summary/);
  },
);

test('an array status cannot be coerced into a valid commit result', async () => {
  const body = buildIntake('Synthetic', [draft()], sources);
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => Response.json({ ...summary(body), status: ['CREATED'] })),
  );
  await expect(sendIntake('commit', body, summary(body))).rejects.toThrow(/invalid intake summary/);
});
