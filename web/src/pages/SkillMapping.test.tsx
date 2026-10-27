import { act, fireEvent, render, screen, within, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, expect, test, vi } from 'vitest';
import { App } from '../App';
import type {
  MappingApproval,
  MappingRecord,
  MappingSelection,
} from '../api/capabilityMappingTypes';
import {
  binding,
  inventoryFinding,
  mappingId,
  mappingOverviewFixture,
  metadataFixture,
  otherHash,
  persistFixture,
  recordFixture,
  skillB,
} from '../test/mappings';

afterEach(() => {
  vi.unstubAllGlobals();
  window.history.replaceState({}, '', '/');
});
type Write = { path: string; body: MappingSelection | MappingApproval };
function setup(
  options: {
    state?: ReturnType<typeof mappingOverviewFixture>;
    post?: (write: Write, persist: () => MappingRecord) => Response | Promise<Response>;
    refreshFails?: () => boolean;
    configured?: boolean;
    snapshots?: boolean;
    listing?: () => Response;
    path?: string;
  } = {},
) {
  const state = options.state ?? mappingOverviewFixture();
  const alternate = mappingOverviewFixture();
  alternate.snapshot_hash = alternate.snapshot.content_hash = otherHash;
  const posts: Write[] = [];
  const gets: string[] = [];
  const fetcher = vi.fn(async (request: Request) => {
    const url = new URL(request.url);
    if (request.method === 'GET') {
      gets.push(url.pathname + url.search);
      if (url.pathname.endsWith('/findings'))
        return Response.json([inventoryFinding(), inventoryFinding('analysis-2')]);
      if (url.pathname.endsWith('/capability-snapshots')) {
        if (options.listing) return options.listing();
        expect(url.search).toBe('?limit=50');
        return Response.json({
          status: 'LISTED',
          snapshots:
            options.snapshots === false ? [] : [metadataFixture(), metadataFixture(otherHash)],
        });
      }
      if (url.pathname.endsWith('/overview')) {
        if (options.refreshFails?.()) return Response.json({ detail: 'PRIVATE' }, { status: 503 });
        const data = structuredClone(
          url.searchParams.get('snapshot_hash') === otherHash ? alternate : state,
        );
        data.analysis_id = url.searchParams.get('analysis_id')!;
        return Response.json(data);
      }
      throw Error('Unexpected GET');
    }
    const body = (await request.clone().json()) as MappingSelection | MappingApproval;
    const write = { path: url.pathname, body };
    posts.push(write);
    function persist() {
      const approval = 'mapping_id' in body;
      const record = {
        ...recordFixture(approval ? 'APPROVED' : 'DRAFT'),
        rationale: body.rationale,
        operator_alias: body.operator_alias,
      };
      if (!approval) record.skill_ids = body.skill_ids;
      record.review_binding = structuredClone(state.findings[0]!.current_review_binding);
      persistFixture(state, record);
      return record;
    }
    return options.post
      ? options.post(write, persist)
      : Response.json(persist(), { status: 'mapping_id' in body ? 200 : 201 });
  });
  vi.stubGlobal('fetch', fetcher);
  vi.stubGlobal('__TENDERCITE_CONFIGURED__', options.configured ?? true);
  window.history.replaceState({}, '', options.path ?? '/tenders/skill-mapping');
  render(<App />);
  return { state, posts, gets, fetcher, user: userEvent.setup() };
}
async function load(
  user: ReturnType<typeof userEvent.setup>,
  hash = metadataFixture().content_hash,
) {
  await user.selectOptions(await screen.findByRole('combobox', { name: 'Analysis' }), 'analysis-1');
  await user.selectOptions(
    await screen.findByRole('combobox', { name: 'Imported snapshot' }),
    hash,
  );
  await user.click(screen.getByRole('button', { name: 'Load mapping overview' }));
  await screen.findByRole('region', { name: 'Mapping overview' });
  await user.selectOptions(screen.getByRole('combobox', { name: 'Requirement' }), 'finding-1');
  return within(screen.getByRole('region', { name: 'Selected mapping' }));
}
async function propose(user: ReturnType<typeof userEvent.setup>) {
  const editor = within(screen.getByRole('region', { name: 'Selected mapping' }));
  await user.click(editor.getByRole('checkbox', { name: /PostgreSQL/ }));
  fireEvent.change(editor.getByLabelText('Mapping rationale'), {
    target: { value: 'Manual synthetic PostgreSQL association.' },
  });
  fireEvent.change(editor.getByLabelText('Non-personal operator alias (optional)'), {
    target: { value: 'operator_a' },
  });
  await user.click(editor.getByRole('button', { name: 'Review proposed mapping' }));
  return screen.getByRole('button', { name: 'Create DRAFT' });
}
async function create(user: ReturnType<typeof userEvent.setup>) {
  await user.click(await propose(user));
  await screen.findByLabelText('Approval rationale');
}
async function approve(user: ReturnType<typeof userEvent.setup>) {
  const editor = within(screen.getByRole('region', { name: 'Selected mapping' }));
  fireEvent.change(editor.getByLabelText('Approval rationale'), {
    target: { value: 'Explicit synthetic approval.' },
  });
  fireEvent.change(editor.getByLabelText('Non-personal operator alias (optional)'), {
    target: { value: 'operator_a' },
  });
  await user.click(editor.getByRole('checkbox'));
  await user.click(editor.getByRole('button', { name: 'APPROVE mapping' }));
}
test('explicit analysis/snapshot selection loads exact scope and source/catalog details without mutations', async () => {
  const { user, posts, gets } = setup();
  expect(screen.getByRole('link', { name: 'Skill Mapping' })).toHaveAttribute(
    'href',
    '/tenders/skill-mapping',
  );
  expect(screen.getByRole('button', { name: 'Load mapping overview' })).toBeDisabled();
  const editor = await load(user);
  expect(editor.getByText(/Mapping state: NOT_FOUND/)).toBeInTheDocument();
  expect(screen.getByRole('link', { name: 'View source PDF page 1' })).toHaveAttribute(
    'href',
    '/tenders/documents/synthetic-doc/pages/1',
  );
  expect(screen.getByRole('region', { name: 'Requirement source evidence' })).toHaveTextContent(
    'Synthetic PostgreSQL requirement.',
  );
  expect(editor.getByRole('checkbox', { name: /Python/ })).not.toBeChecked();
  expect(editor.getByRole('checkbox', { name: /PostgreSQL/ })).not.toBeChecked();
  const read = new URL(
    gets.find((path) => path.includes('/overview'))!,
    'http://localhost',
  );
  expect([...read.searchParams]).toEqual([
    ['analysis_id', 'analysis-1'],
    ['snapshot_hash', metadataFixture().content_hash],
  ]);
  expect(posts).toHaveLength(0);
});
test('analysis link preselects the analysis but never a snapshot or automatic overview request', async () => {
  const { gets } = setup({ path: '/tenders/skill-mapping?analysis_id=analysis-1' });
  await waitFor(() =>
    expect(screen.getByRole('combobox', { name: 'Analysis' })).toHaveValue('analysis-1'),
  );
  expect(await screen.findByRole('combobox', { name: 'Imported snapshot' })).toHaveValue('');
  expect(gets.some((path) => path.includes('/overview'))).toBe(false);
});
test('unconfigured backend makes no requests and does not expose mapping controls', () => {
  const { fetcher } = setup({ configured: false });
  expect(screen.getByText(/TenderCite is not configured/)).toBeInTheDocument();
  expect(screen.queryByRole('button', { name: 'Load mapping overview' })).not.toBeInTheDocument();
  expect(fetcher).not.toHaveBeenCalled();
});
test('no imported snapshots prevents loading a mapping overview', async () => {
  const { posts } = setup({ snapshots: false });
  await screen.findByText('No imported snapshots available.');
  expect(screen.getByRole('button', { name: 'Load mapping overview' })).toBeDisabled();
  expect(posts).toHaveLength(0);
});
test('disabled mapping API shows a fixed safe code without enabling writes', async () => {
  const { posts } = setup({
    listing: () =>
      Response.json(
        { detail: { code: 'CAPABILITY_MAPPING_API_DISABLED', message: 'PRIVATE' } },
        { status: 404 },
      ),
  });
  expect(await screen.findByRole('alert')).toHaveTextContent('CAPABILITY_MAPPING_API_DISABLED');
  expect(document.body.textContent).not.toContain('PRIVATE');
  expect(screen.getByRole('button', { name: 'Load mapping overview' })).toBeDisabled();
  expect(posts).toHaveLength(0);
});
test('revoked mappings remain inspectable and cannot be recreated or reactivated', async () => {
  const state = mappingOverviewFixture();
  persistFixture(state, recordFixture());
  state.findings[0]!.mapping.status = 'REVOKED';
  state.findings[0]!.mapping.unavailable_reasons = ['MAPPING_REVOKED'];
  const { user, posts } = setup({ state });
  const editor = await load(user);
  expect(editor.getByText('Mapping state: REVOKED')).toBeInTheDocument();
  expect(editor.queryByRole('button')).not.toBeInTheDocument();
  expect(posts).toHaveLength(0);
});
test('proposal is inspectable, edits invalidate it, and DRAFT creation never approves automatically', async () => {
  const { user, posts } = setup();
  await load(user);
  await propose(user);
  const preview = within(screen.getByRole('region', { name: 'Proposed mapping' }));
  expect(preview.getByText(/not saved/)).toBeInTheDocument();
  expect(preview.getByText(new RegExp(skillB))).toBeInTheDocument();
  expect(posts).toHaveLength(0);
  fireEvent.change(screen.getByLabelText('Mapping rationale'), {
    target: { value: 'Changed rationale.' },
  });
  expect(screen.queryByRole('button', { name: 'Create DRAFT' })).not.toBeInTheDocument();
  await user.click(screen.getByRole('button', { name: 'Review proposed mapping' }));
  await user.click(screen.getByRole('button', { name: 'Create DRAFT' }));
  await screen.findByLabelText('Approval rationale');
  expect(posts).toEqual([
    {
      path: '/tendercite-api/api/v1/capability-mappings/drafts',
      body: {
        schema_version: 'capability-workflow-mapping-selection-1',
        analysis_id: 'analysis-1',
        snapshot_hash: metadataFixture().content_hash,
        finding_id: 'finding-1',
        operator: 'ALL_OF',
        skill_ids: [skillB],
        rationale: 'Changed rationale.',
        operator_alias: 'operator_a',
      },
    },
  ]);
  const editor = within(screen.getByRole('region', { name: 'Selected mapping' }));
  expect(editor.getByText(/Mapping state: DRAFT/)).toBeInTheDocument();
  expect(
    editor.getByText(new RegExp(`Mapping ID: ${mappingId} · Revision: 1`)),
  ).toBeInTheDocument();
  expect(editor.getByRole('checkbox')).not.toBeChecked();
  expect(editor.getByRole('button', { name: 'APPROVE mapping' })).toBeDisabled();
});
test('explicit APPROVE sends exact revision/review binding and reads back operator approval', async () => {
  const { user, posts, gets } = setup();
  await load(user);
  await create(user);
  expect(posts).toHaveLength(1);
  await approve(user);
  await screen.findByText(/Server confirmed APPROVED/);
  const editor = within(screen.getByRole('region', { name: 'Selected mapping' }));
  await waitFor(() => expect(editor.getByText(/Mapping state: APPROVED/)).toBeInTheDocument());
  expect(posts[1]).toEqual({
    path: `/tendercite-api/api/v1/capability-mappings/${mappingId}/approve`,
    body: {
      schema_version: 'capability-mapping-approval-1',
      mapping_id: mappingId,
      expected_revision: 1,
      expected_review_event_id: binding().review_event_id,
      expected_review_digest: binding().digest,
      rationale: 'Explicit synthetic approval.',
      operator_alias: 'operator_a',
    },
  });
  expect(gets.filter((path) => path.includes('/overview'))).toHaveLength(3);
  expect(
    editor.getByText(/not independently verified professional qualification/),
  ).toBeInTheDocument();
  expect(editor.queryByRole('button', { name: 'APPROVE mapping' })).not.toBeInTheDocument();
});
test.each(['UNREVIEWED', 'REJECTED', 'INVALID_QUOTE'])(
  '%s requirement is visible but cannot be mapped or approved',
  async (kind) => {
    const state = mappingOverviewFixture();
    const f = state.findings[0]!;
    if (kind === 'INVALID_QUOTE') f.grounding_status = kind;
    else f.review_status = kind;
    if (kind === 'UNREVIEWED') f.current_review_binding.review_event_id = null;
    f.mapping_preparation_eligible = false;
    const { user, posts } = setup({ state });
    const editor = await load(user);
    expect(editor.getByRole('note')).toHaveTextContent('Not eligible for mapping');
    expect(editor.queryByRole('button')).not.toBeInTheDocument();
    expect(posts).toHaveLength(0);
  },
);
test('changing snapshots clears old proposals and never substitutes another snapshot mapping', async () => {
  const { user, posts } = setup();
  await load(user);
  await propose(user);
  await user.selectOptions(screen.getByRole('combobox', { name: 'Imported snapshot' }), otherHash);
  expect(screen.queryByRole('button', { name: 'Create DRAFT' })).not.toBeInTheDocument();
  expect(screen.queryByRole('region', { name: 'Mapping overview' })).not.toBeInTheDocument();
  await user.click(screen.getByRole('button', { name: 'Load mapping overview' }));
  await screen.findByRole('region', { name: 'Mapping overview' });
  expect(screen.getByRole('region', { name: 'Mapping overview' })).toHaveTextContent(otherHash);
  await user.selectOptions(screen.getByRole('combobox', { name: 'Requirement' }), 'finding-1');
  expect(screen.getByRole('region', { name: 'Selected mapping' })).toHaveTextContent(
    'Mapping state: NOT_FOUND',
  );
  expect(posts).toHaveLength(0);
});
test('a new CONFIRM with identical reviewed text makes the approval STALE after reload', async () => {
  const state = mappingOverviewFixture();
  persistFixture(state, recordFixture('APPROVED'));
  const { user, posts } = setup({ state });
  await load(user);
  expect(screen.getByRole('region', { name: 'Selected mapping' })).toHaveTextContent(
    'Mapping state: APPROVED',
  );
  state.findings[0]!.current_review_binding.review_event_id = 'review-2';
  state.findings[0]!.mapping.status = 'STALE';
  state.findings[0]!.mapping.unavailable_reasons = ['REQUIREMENT_REVIEW_EVENT_CHANGED'];
  await user.click(screen.getByRole('button', { name: 'Reload current overview' }));
  await waitFor(() =>
    expect(screen.getByRole('region', { name: 'Selected mapping' })).toHaveTextContent(
      'Mapping state: STALE',
    ),
  );
  expect(screen.getByRole('region', { name: 'Selected mapping' })).toHaveTextContent(
    'REQUIREMENT_REVIEW_EVENT_CHANGED',
  );
  expect(screen.queryByRole('button', { name: 'APPROVE mapping' })).not.toBeInTheDocument();
  expect(posts).toHaveLength(0);
});
test.each(['MAPPING_REVISION_CONFLICT', 'REQUIREMENT_REVIEW_CONFLICT'])(
  '%s does not retry approval and refreshes state',
  async (code) => {
    const state = mappingOverviewFixture();
    persistFixture(state, recordFixture());
    const { user, posts, gets } = setup({
      state,
      post: () => Response.json({ detail: { code, message: 'PRIVATE' } }, { status: 409 }),
    });
    await load(user);
    await approve(user);
    expect(await screen.findByRole('alert')).toHaveTextContent(code);
    await waitFor(() => expect(gets.filter((path) => path.includes('/overview'))).toHaveLength(2));
    expect(posts).toHaveLength(1);
    expect(document.body.textContent).not.toContain('PRIVATE');
    await screen.findByLabelText('Approval rationale');
    expect(
      within(screen.getByRole('region', { name: 'Selected mapping' })).getByRole('checkbox'),
    ).not.toBeChecked();
  },
);
test.each(['UNKNOWN_CATALOG_SKILL_UUID', 'MAPPING_ALREADY_EXISTS'])(
  'draft error %s preserves explicit failure and reloads without retries',
  async (code) => {
    const { user, posts, gets } = setup({
      post: () => Response.json({ detail: { code, message: 'PRIVATE' } }, { status: 422 }),
    });
    await load(user);
    await user.click(await propose(user));
    expect(await screen.findByRole('alert')).toHaveTextContent(code);
    await waitFor(() => expect(gets.filter((path) => path.includes('/overview'))).toHaveLength(2));
    expect(posts).toHaveLength(1);
    expect(document.body.textContent).not.toContain('PRIVATE');
  },
);
test.each(['DRAFT', 'APPROVED'] as const)(
  'lost %s response can have persisted; authoritative state is reloaded without an automatic retry',
  async (status) => {
    const state = mappingOverviewFixture();
    if (status === 'APPROVED') persistFixture(state, recordFixture());
    const { user, posts } = setup({
      state,
      post: (_write, persist) => {
        persist();
        throw Error('PRIVATE lost response');
      },
    });
    await load(user);
    if (status === 'DRAFT') await user.click(await propose(user));
    else await approve(user);
    expect(await screen.findByRole('alert')).toHaveTextContent('Save outcome is uncertain');
    await waitFor(() =>
      expect(screen.getByRole('region', { name: 'Selected mapping' })).toHaveTextContent(
        `Mapping state: ${status}`,
      ),
    );
    expect(screen.queryByText(/Server confirmed/)).not.toBeInTheDocument();
    expect(posts).toHaveLength(1);
    expect(document.body.textContent).not.toContain('PRIVATE');
  },
);
test('a failed authoritative refresh disables cached actions until a successful explicit reload', async () => {
  let refreshFails = false;
  const { user, posts } = setup({
    refreshFails: () => refreshFails,
    post: (_write, persist) => {
      const record = persist();
      refreshFails = true;
      return Response.json(record);
    },
  });
  await load(user);
  await user.click(await propose(user));
  await screen.findByText(/Cached overview may be stale/);
  expect(screen.getByRole('button', { name: 'Create DRAFT' })).toBeDisabled();
  refreshFails = false;
  await user.click(screen.getByRole('button', { name: 'Reload current overview' }));
  await screen.findByLabelText('Approval rationale');
  expect(posts).toHaveLength(1);
});
test('pending mutations prevent duplicate writes, overlapping refreshes and selection changes', async () => {
  let finish!: (value: Response) => void;
  const pending = new Promise<Response>((resolve) => {
    finish = resolve;
  });
  let record: MappingRecord;
  const { user, posts } = setup({
    post: (_write, persist) => {
      record = persist();
      return pending;
    },
  });
  await load(user);
  const button = await propose(user);
  fireEvent.click(button);
  fireEvent.click(button);
  await waitFor(() => expect(posts).toHaveLength(1));
  expect(screen.getByRole('combobox', { name: 'Analysis' })).toBeDisabled();
  expect(screen.getByRole('button', { name: 'Reload current overview' })).toBeDisabled();
  await act(async () => finish(Response.json(record!)));
  await screen.findByLabelText('Approval rationale');
  expect(posts).toHaveLength(1);
});
