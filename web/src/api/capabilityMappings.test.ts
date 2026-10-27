import { afterEach, expect, test, vi } from 'vitest';
import {
  mappingAnalyses,
  mappingSnapshots,
  mappingOverview,
  proposeMapping,
  createMappingDraft,
  approvalInput,
  approveMapping,
} from './capabilityMappings';
import {
  binding,
  inventoryFinding,
  mappingId,
  mappingOverviewFixture,
  metadataFixture,
  otherHash,
  persistFixture,
  recordFixture,
  skillA,
  skillB,
} from '../test/mappings';
import type { MappingSelection } from './capabilityMappingTypes';
afterEach(() => vi.unstubAllGlobals());
const signal = () => new AbortController().signal;
const mock = (reply: (request: Request) => Response | Promise<Response>) => {
  const fetcher = vi.fn(async (request: Request) => reply(request));
  vi.stubGlobal('fetch', fetcher);
  return fetcher;
};
test('analysis inventory derives IDs from findings without assuming an analysis-list API', async () => {
  const fetcher = mock(() => Response.json([inventoryFinding('analysis-2'), inventoryFinding()]));
  expect(await mappingAnalyses(signal())).toEqual(['analysis-1', 'analysis-2']);
  expect(fetcher.mock.calls[0]![0].url).toMatch(/\/api\/v1\/findings$/);
});
test('snapshot listing is bounded metadata-only and scoped overview uses exact encoded identities', async () => {
  const overview = mappingOverviewFixture();
  overview.analysis_id = 'analysis:1';
  const fetcher = mock((req) =>
    Response.json(
      req.url.includes('capability-snapshots')
        ? { status: 'LISTED', snapshots: [{ ...metadataFixture(), person_id: 'PRIVATE' }] }
        : overview,
    ),
  );
  expect(await mappingSnapshots(signal())).toEqual([metadataFixture()]);
  expect(fetcher.mock.calls[0]![0].url).toMatch(/capability-snapshots\?limit=50$/);
  expect(await mappingOverview(overview, signal())).toEqual(overview);
  const url = new URL(fetcher.mock.calls[1]![0].url);
  expect([...url.searchParams]).toEqual([
    ['analysis_id', 'analysis:1'],
    ['snapshot_hash', overview.snapshot_hash],
  ]);
});
test.each(['bad-hash', 'duplicate', 'oversized', 'period', 'status'])(
  'invalid %s snapshot listing is rejected safely',
  async (kind) => {
    const data = { status: 'LISTED', snapshots: [metadataFixture()] };
    if (kind === 'bad-hash') data.snapshots[0]!.content_hash = 'PRIVATE';
    if (kind === 'duplicate') data.snapshots.push(metadataFixture());
    if (kind === 'oversized') data.snapshots = Array.from({ length: 51 }, () => metadataFixture());
    if (kind === 'period') data.snapshots[0]!.assessment_period_end = '2027-01-01';
    if (kind === 'status') data.status = 'PRIVATE';
    mock(() => Response.json(data));
    await expect(mappingSnapshots(signal())).rejects.toThrow('Invalid imported snapshot metadata.');
  },
);
test.each([
  'analysis',
  'hash',
  'finding-scope',
  'catalog',
  'eligible',
  'binding-value',
  'approved-binding',
])('invalid/mismatched %s overview never authorizes a mapping', async (kind) => {
  const data = mappingOverviewFixture();
  const scope = { analysis_id: data.analysis_id, snapshot_hash: data.snapshot_hash };
  if (kind === 'analysis') data.analysis_id = 'analysis-2';
  if (kind === 'hash') data.snapshot_hash = data.snapshot.content_hash = otherHash;
  if (kind === 'finding-scope') data.analysis_finding_ids = ['different-finding'];
  if (kind === 'catalog') data.snapshot.skill_catalog.push({ ...data.snapshot.skill_catalog[0]! });
  if (kind === 'eligible') data.findings[0]!.review_status = 'REJECTED';
  if (kind === 'binding-value')
    data.findings[0]!.current_review_binding.value.statement = 'PRIVATE';
  if (kind === 'approved-binding') {
    persistFixture(data, recordFixture('APPROVED'));
    data.findings[0]!.current_review_binding.review_event_id = 'new-review';
  }
  mock(() => Response.json(data));
  await expect(mappingOverview(scope, signal())).rejects.toThrow(
    /Invalid or mismatched mapping overview/,
  );
});
test('draft proposal canonicalizes explicit UUIDs and preserves operator text without inventing wire fields', async () => {
  const overview = mappingOverviewFixture();
  const finding = overview.findings[0]!;
  const proposal = proposeMapping(
    overview,
    finding,
    [skillB, skillA],
    '  Synthetic rationale.\n',
    'operator_a',
  );
  expect(proposal.selection).toEqual({
    schema_version: 'capability-workflow-mapping-selection-1',
    analysis_id: overview.analysis_id,
    snapshot_hash: overview.snapshot_hash,
    finding_id: finding.finding_id,
    operator: 'ALL_OF',
    skill_ids: [skillA, skillB],
    rationale: '  Synthetic rationale.\n',
    operator_alias: 'operator_a',
  });
  expect(proposal.selection).not.toHaveProperty('expected_review_event_id');
  const fetcher = mock(async (request) =>
    Response.json({
      ...recordFixture(),
      ...((await request.clone().json()) as MappingSelection),
      schema_version: 'capability-mapping-record-1',
    }),
  );
  const result = await createMappingDraft(proposal);
  expect(result.status).toBe('DRAFT');
  const req = fetcher.mock.calls[0]![0];
  expect(req.url).toBe(
    `${window.location.origin}/tendercite-api/api/v1/capability-mappings/drafts`,
  );
  expect(await req.clone().json()).toEqual(proposal.selection);
  expect(req.credentials).toBe('omit');
  expect(req.redirect).toBe('error');
  expect(req.cache).toBe('no-store');
  expect(fetcher).toHaveBeenCalledTimes(1);
});
test.each([
  'empty',
  'duplicate',
  'unknown',
  'too-many',
  'rationale',
  'alias',
  'ineligible',
  'cross-finding',
  'existing',
])('invalid %s selection fails before network access', (kind) => {
  const overview = mappingOverviewFixture();
  let finding = overview.findings[0]!;
  let skills = [skillB];
  let rationale = 'Synthetic rationale.';
  let alias = 'operator_a';
  if (kind === 'empty') skills = [];
  if (kind === 'duplicate') skills = [skillB, skillB];
  if (kind === 'unknown') skills = [mappingId];
  if (kind === 'too-many') skills = Array.from({ length: 101 }, () => skillB);
  if (kind === 'rationale') rationale = ' ';
  if (kind === 'alias') alias = 'PRIVATE@example.com';
  if (kind === 'ineligible') finding.mapping_preparation_eligible = false;
  if (kind === 'cross-finding') finding = structuredClone(finding);
  if (kind === 'existing') persistFixture(overview, recordFixture());
  const fetcher = mock(() => Response.json({}));
  expect(() => proposeMapping(overview, finding, skills, rationale, alias)).toThrow();
  expect(fetcher).not.toHaveBeenCalled();
});
test('approval includes the exact current revision and review binding, and validates the new revision', async () => {
  const overview = mappingOverviewFixture();
  persistFixture(overview, recordFixture());
  const finding = overview.findings[0]!;
  const input = approvalInput(overview, finding, 'Synthetic rationale.', 'operator_a');
  expect(input).toEqual({
    schema_version: 'capability-mapping-approval-1',
    mapping_id: mappingId,
    expected_revision: 1,
    expected_review_event_id: binding().review_event_id,
    expected_review_digest: binding().digest,
    rationale: 'Synthetic rationale.',
    operator_alias: 'operator_a',
  });
  const fetcher = mock(() => Response.json(recordFixture('APPROVED')));
  expect(
    (await approveMapping(overview, finding, 'Synthetic rationale.', 'operator_a')).revision,
  ).toBe(2);
  const request = fetcher.mock.calls[0]![0];
  expect(await request.json()).toEqual(input);
  expect(request.url).toBe(
    `${window.location.origin}/tendercite-api/api/v1/capability-mappings/${mappingId}/approve`,
  );
  expect(request.redirect).toBe('error');
  expect(request.credentials).toBe('omit');
});
test.each(['NOT_FOUND', 'APPROVED', 'STALE', 'REVOKED'] as const)(
  '%s mapping cannot be approved as a draft',
  (status) => {
    const overview = mappingOverviewFixture();
    persistFixture(overview, recordFixture());
    overview.findings[0]!.mapping.status = status;
    const fetcher = mock(() => Response.json({}));
    expect(() =>
      approvalInput(overview, overview.findings[0]!, 'Synthetic rationale.', ''),
    ).toThrow();
    expect(fetcher).not.toHaveBeenCalled();
  },
);
test.each(['binding', 'snapshot', 'skills', 'scope', 'status'])(
  'mismatched draft %s response is uncertain and never retried',
  async (kind) => {
    const overview = mappingOverviewFixture();
    const proposal = proposeMapping(
      overview,
      overview.findings[0]!,
      [skillB],
      'Synthetic rationale.',
      'operator_a',
    );
    const record = recordFixture();
    if (kind === 'binding') record.review_binding.review_event_id = 'review-2';
    if (kind === 'snapshot') record.snapshot_hash = otherHash;
    if (kind === 'skills') record.skill_ids = [skillA];
    if (kind === 'scope') record.analysis_id = 'analysis-2';
    if (kind === 'status') record.status = 'REVOKED';
    const fetcher = mock(() => Response.json(record));
    await expect(createMappingDraft(proposal)).rejects.toThrow(/Draft confirmation differs/);
    expect(fetcher).toHaveBeenCalledTimes(1);
  },
);
test.each(['revision', 'review', 'scope', 'skills', 'mapping-id'])(
  'mismatched approval %s response cannot confirm success',
  async (kind) => {
    const overview = mappingOverviewFixture();
    persistFixture(overview, recordFixture());
    const record = recordFixture('APPROVED');
    if (kind === 'revision') record.revision = 4;
    if (kind === 'review') record.review_binding.review_event_id = 'different-review';
    if (kind === 'scope') record.analysis_id = 'different-analysis';
    if (kind === 'skills') record.skill_ids = [skillA];
    if (kind === 'mapping-id') record.mapping_id = skillA;
    const fetcher = mock(() => Response.json(record));
    await expect(
      approveMapping(overview, overview.findings[0]!, 'Synthetic rationale.', 'operator_a'),
    ).rejects.toThrow(/Approval confirmation differs/);
    expect(fetcher).toHaveBeenCalledTimes(1);
  },
);
test.each([
  'MAPPING_REVISION_CONFLICT',
  'REQUIREMENT_REVIEW_CONFLICT',
  'UNKNOWN_CATALOG_SKILL_UUID',
  'CAPABILITY_MAPPING_API_DISABLED',
  'PRIVATE',
])('HTTP %s is privacy safe with no retry', async (code) => {
  const overview = mappingOverviewFixture();
  const proposal = proposeMapping(
    overview,
    overview.findings[0]!,
    [skillB],
    'Synthetic rationale.',
    'operator_a',
  );
  const fetcher = mock(() =>
    Response.json({ detail: { code, message: 'PRIVATE' } }, { status: 409 }),
  );
  await expect(createMappingDraft(proposal)).rejects.toThrow(
    code === 'PRIVATE'
      ? 'Mapping request failed (HTTP 409).'
      : `Mapping request failed (HTTP 409): ${code}.`,
  );
  expect(fetcher).toHaveBeenCalledTimes(1);
});
test.each(['network', 'HTML', 'JSON'])(
  '%s mutation response failure is sanitized and never retried',
  async (kind) => {
    const overview = mappingOverviewFixture();
    const proposal = proposeMapping(
      overview,
      overview.findings[0]!,
      [skillB],
      'Synthetic rationale.',
      'operator_a',
    );
    const fetcher = mock(() => {
      if (kind === 'network') throw Error('PRIVATE');
      return new Response('PRIVATE', {
        headers: { 'content-type': kind === 'HTML' ? 'text/html' : 'application/json' },
      });
    });
    await expect(createMappingDraft(proposal)).rejects.toThrow(
      'Mapping response unavailable or invalid.',
    );
    expect(fetcher).toHaveBeenCalledTimes(1);
  },
);
