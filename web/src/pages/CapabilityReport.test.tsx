import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, expect, test, vi } from 'vitest';
import { App } from '../App';
import { CapabilityReport } from './CapabilityReport';
import type { AssessmentSelection } from '../api/capabilityReportTypes';
import { inventoryFinding, mappingFinding, metadataFixture, otherHash } from '../test/mappings';
import { reportFixture } from '../test/reports';

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  window.history.replaceState({}, '', '/');
});
function setup(
  options: {
    count?: number;
    configured?: boolean;
    discoveryDisabled?: boolean;
    report?: (body: AssessmentSelection) => Response | Promise<Response>;
    findings?: () => Response;
    path?: string;
    client?: QueryClient;
  } = {},
) {
  const posts: AssessmentSelection[] = [];
  const requests: Request[] = [];
  const fetcher = vi.fn(async (request: Request) => {
    requests.push(request);
    const url = new URL(request.url);
    if (request.method === 'POST') {
      expect(url.pathname).toBe('/tendercite-api/api/v1/capability-workflow/report');
      const body = (await request.json()) as AssessmentSelection;
      posts.push(body);
      if (options.report) return options.report(body);
      const report = reportFixture(body.finding_ids);
      report.analysis_id = body.analysis_id;
      report.snapshot_hash = report.snapshot.content_hash = body.snapshot_hash;
      return Response.json(report);
    }
    if (url.pathname.endsWith('/capability-snapshots'))
      return options.discoveryDisabled
        ? Response.json({ detail: { code: 'CAPABILITY_MAPPING_API_DISABLED' } }, { status: 404 })
        : Response.json({
            status: 'LISTED',
            snapshots: [metadataFixture(), metadataFixture(otherHash)],
          });
    if (url.pathname.endsWith('/findings')) {
      const analysis = url.searchParams.get('analysis_run_id');
      if (!analysis) return Response.json([inventoryFinding(), inventoryFinding('analysis-2')]);
      return options.findings
        ? options.findings()
        : Response.json(
            Array.from({ length: options.count ?? 2 }, (_, i) => ({
              ...inventoryFinding(analysis),
              id: `finding-${i + 1}`,
            })),
          );
    }
    if (url.pathname.endsWith('/pages/1'))
      return Response.json({
        document_id: 'synthetic-doc',
        page_number: 1,
        text: 'Synthetic source page.',
      });
    throw Error('Unexpected synthetic request');
  });
  vi.stubGlobal('fetch', fetcher);
  vi.stubGlobal('__TENDERCITE_CONFIGURED__', options.configured ?? true);
  window.history.replaceState({}, '', options.path ?? '/tenders/capability-report');
  render(
    options.client ? (
      <QueryClientProvider client={options.client}>
        <MemoryRouter>
          <CapabilityReport />
        </MemoryRouter>
      </QueryClientProvider>
    ) : (
      <App />
    ),
  );
  return { posts, requests, fetcher, user: userEvent.setup() };
}
async function selectScope(user: ReturnType<typeof userEvent.setup>) {
  await user.selectOptions(await screen.findByRole('combobox', { name: 'Analysis' }), 'analysis-1');
  await user.selectOptions(
    screen.getByRole('combobox', { name: 'Imported snapshot' }),
    metadataFixture().content_hash,
  );
  await screen.findByRole('checkbox', { name: /finding-1$/ });
}
const run = () => screen.getByRole('button', { name: 'Run Capability Matching' });
const checkbox = (id = 'finding-1') => screen.getByRole('checkbox', { name: new RegExp(`${id}$`) });
const results = () => screen.queryByRole('region', { name: 'Capability matching report' });

test('navigation, explicit scope and requirement selection produce exactly the selected versioned request', async () => {
  const { user, posts } = setup();
  expect(screen.getByRole('link', { name: 'Capability Matching' })).toHaveAttribute(
    'href',
    '/tenders/capability-report',
  );
  expect(run()).toBeDisabled();
  await selectScope(user);
  expect(posts).toHaveLength(0);
  expect(screen.getAllByRole('checkbox').every((c) => !(c as HTMLInputElement).checked)).toBe(true);
  await user.click(checkbox());
  await user.click(run());
  await screen.findByRole('region', { name: 'Capability matching report' });
  expect(posts).toEqual([
    {
      schema_version: 'capability-workflow-assessment-selection-1',
      analysis_id: 'analysis-1',
      snapshot_hash: metadataFixture().content_hash,
      finding_ids: ['finding-1'],
    },
  ]);
  expect(screen.getByRole('list', { name: 'Report summary' })).toHaveTextContent(
    'Selected requirements: 1',
  );
  expect(screen.queryByRole('article', { name: 'Requirement finding-2' })).not.toBeInTheDocument();
});
test('approved result exposes backend individual coverage only on expansion, with missing assignments and source links', async () => {
  const { user } = setup();
  await selectScope(user);
  await user.click(checkbox());
  await user.click(run());
  const row = within(await screen.findByRole('article', { name: 'Requirement finding-1' }));
  expect(row.getByText('Mapping: APPROVED')).toBeInTheDocument();
  expect(row.getByText('Recorded coverage: ALL_REQUIRED_SKILLS_RECORDED')).toBeInTheDocument();
  expect(row.getByText(/All required skills recorded: 1/)).toHaveTextContent(
    'Some required skills recorded: 1',
  );
  const person = '00000000-0000-0000-0000-000000000002';
  expect(row.queryByText(new RegExp(person))).not.toBeInTheDocument();
  await user.click(row.getByText('Individual recorded-skill coverage (3)'));
  expect(await row.findByText(new RegExp(person))).toHaveTextContent(
    'SOME_REQUIRED_SKILLS_RECORDED',
  );
  expect(row.getAllByText(/Missing recorded assignments:/)).toHaveLength(3);
  await user.click(row.getByText('Original requirement and source quotations'));
  const source = row.getByRole('link', { name: 'View source PDF page 1' });
  expect(source).toHaveAttribute('href', '/tenders/documents/synthetic-doc/pages/1');
  expect(
    row.getByRole('link', { name: 'Finding detail, review and source evidence' }),
  ).toHaveAttribute('href', '/tenders/findings/finding-1');
  expect(row.getByRole('list', { name: 'Evidence limitations' })).toHaveTextContent(
    'Proficiency is not verified years',
  );
  expect(row.getByText(/Qualification: NOT_VERIFIED/)).toHaveTextContent('Capacity: NOT_EVALUATED');
  await user.click(source);
  expect(await screen.findByText('Synthetic source page.')).toBeInTheDocument();
});
test.each(['SOME_REQUIRED_SKILLS_RECORDED', 'NO_REQUIRED_SKILLS_RECORDED'] as const)(
  '%s is displayed as recorded coverage without a new recommendation',
  async (coverage) => {
    const report = reportFixture();
    report.findings[0]!.coverage = coverage;
    const rowData = report.findings[0]!;
    if (coverage === 'SOME_REQUIRED_SKILLS_RECORDED') {
      rowData.individual_coverage[0]!.matched_skill_ids = [
        ...rowData.individual_coverage[1]!.matched_skill_ids,
      ];
      rowData.individual_coverage[0]!.missing_assignment_ids = [
        ...rowData.individual_coverage[1]!.missing_assignment_ids,
      ];
      rowData.individual_coverage[0]!.coverage = coverage;
      rowData.individual_counts = {
        all_required_recorded: 0,
        some_required_recorded: 2,
        no_required_recorded: 1,
      };
    } else {
      rowData.individual_coverage.forEach((person) => {
        person.matched_skill_ids = [];
        person.missing_assignment_ids = [...rowData.finding.mapping.skill_ids];
        person.coverage = coverage;
      });
      rowData.individual_counts = {
        all_required_recorded: 0,
        some_required_recorded: 0,
        no_required_recorded: 3,
      };
    }
    const { user } = setup({ report: () => Response.json(report) });
    await selectScope(user);
    await user.click(checkbox());
    await user.click(run());
    const row = within(await screen.findByRole('article', { name: 'Requirement finding-1' }));
    expect(row.getByText(`Recorded coverage: ${coverage}`)).toBeInTheDocument();
    expect(row.getByText(/Qualification: NOT_VERIFIED/)).toBeInTheDocument();
    expect(screen.getByText(/Missing assignments do not prove absent skills/)).toBeInTheDocument();
  },
);
function unavailableReport(state: 'NOT_FOUND' | 'DRAFT' | 'STALE' | 'REVOKED' | 'UNREVIEWED') {
  const report = reportFixture();
  const row = report.findings[0]!;
  if (state === 'NOT_FOUND' || state === 'UNREVIEWED') row.finding = mappingFinding();
  else {
    row.finding.mapping.status = state;
    row.finding.mapping.unavailable_reasons = [
      state === 'DRAFT'
        ? 'MAPPING_NOT_APPROVED'
        : state === 'REVOKED'
          ? 'MAPPING_REVOKED'
          : 'REQUIREMENT_REVIEW_EVENT_CHANGED',
    ];
    if (state === 'STALE') row.finding.current_review_binding.review_event_id = 'new-review';
    else row.finding.mapping.approved_review_binding = null;
  }
  if (state === 'UNREVIEWED') {
    row.finding.review_status = 'UNREVIEWED';
    row.finding.current_review_binding.review_event_id = null;
    row.finding.mapping_preparation_eligible = false;
  }
  row.coverage = 'NOT_EVALUATED';
  row.individual_coverage = [];
  row.individual_counts = {
    all_required_recorded: 0,
    some_required_recorded: 0,
    no_required_recorded: 0,
  };
  row.non_evaluation_reasons = [
    state === 'UNREVIEWED'
      ? 'UNREVIEWED_FINDING'
      : state === 'NOT_FOUND'
        ? 'NO_EXPLICIT_SKILL_MAPPING'
        : `PERSISTED_MAPPING_${state === 'DRAFT' ? 'NOT_APPROVED' : state}`,
  ];
  report.summary.current_approved = 0;
  report.summary.not_eligible_for_matching = 1;
  if (state === 'DRAFT') report.summary.drafts = 1;
  if (state === 'STALE') report.summary.stale = 1;
  if (state === 'REVOKED') report.summary.revoked = 1;
  if (state === 'NOT_FOUND' || state === 'UNREVIEWED') report.summary.without_mapping = 1;
  return report;
}
test.each(['NOT_FOUND', 'DRAFT', 'STALE', 'REVOKED', 'UNREVIEWED'] as const)(
  '%s remains visible and NOT_EVALUATED, without approval or matching fallbacks',
  async (state) => {
    const report = unavailableReport(state);
    const { user, posts } = setup({ report: () => Response.json(report) });
    await selectScope(user);
    await user.click(checkbox());
    await user.click(run());
    const row = within(await screen.findByRole('article', { name: 'Requirement finding-1' }));
    expect(row.getByText('Recorded coverage: NOT_EVALUATED')).toBeInTheDocument();
    expect(
      row.getByText(`Mapping: ${report.findings[0]!.finding.mapping.status}`),
    ).toBeInTheDocument();
    expect(row.getByText(/Not evaluated because/)).toHaveTextContent(
      report.findings[0]!.non_evaluation_reasons[0]!,
    );
    expect(row.queryByText(/Individual recorded-skill coverage/)).not.toBeInTheDocument();
    expect(posts).toHaveLength(1);
  },
);
test('empty selection is an explicit no-op report and never selects all findings', async () => {
  const { user, posts } = setup();
  await selectScope(user);
  expect(screen.getByText(/No requirements selected/)).toBeInTheDocument();
  await user.click(run());
  await screen.findByText('Empty report: no requirements were selected or evaluated.');
  expect(posts[0]!.finding_ids).toEqual([]);
  expect(screen.queryByRole('article')).not.toBeInTheDocument();
});
test('100-finding limit permits deselection and rejects selecting a 101st finding', async () => {
  const { user, posts } = setup({ count: 101 });
  await selectScope(user);
  const firstHundred = screen.getAllByRole('checkbox', { name: /finding-(?:[1-9][0-9]?|100)$/ });
  expect(firstHundred).toHaveLength(100);
  act(() => {
    for (const choice of firstHundred) fireEvent.click(choice);
  });
  expect(checkbox('finding-101')).toBeDisabled();
  await user.click(run());
  await screen.findByRole('region', { name: 'Capability matching report' });
  expect(posts[0]!.finding_ids).toHaveLength(100);
  await user.click(checkbox());
  expect(checkbox('finding-101')).toBeEnabled();
  expect(results()).not.toBeInTheDocument();
});
test('exact-hash fallback works when snapshot discovery is disabled without enabling its flag', async () => {
  const { user, posts } = setup({ discoveryDisabled: true });
  await screen.findByText(/Snapshot discovery unavailable/);
  await user.selectOptions(await screen.findByRole('combobox', { name: 'Analysis' }), 'analysis-1');
  await user.type(
    screen.getByRole('textbox', { name: 'Exact snapshot hash' }),
    metadataFixture().content_hash,
  );
  await user.click(await screen.findByRole('checkbox', { name: /finding-1$/ }));
  await user.click(run());
  await screen.findByRole('region', { name: 'Capability matching report' });
  expect(posts[0]!.snapshot_hash).toBe(metadataFixture().content_hash);
});
test.each(['disabled', 'network', 'malformed', 'mismatch', 'http'] as const)(
  '%s report failure is safe, keeps selection, and never retries',
  async (kind) => {
    const { user, posts } = setup({
      report: () => {
        if (kind === 'network') throw Error('PRIVATE');
        if (kind === 'disabled')
          return Response.json(
            { detail: { code: 'CAPABILITY_REPORT_API_DISABLED' } },
            { status: 404 },
          );
        if (kind === 'http')
          return Response.json(
            { detail: { code: 'PRIVATE', message: 'PRIVATE' } },
            { status: 503 },
          );
        if (kind === 'malformed')
          return new Response('PRIVATE', { headers: { 'Content-Type': 'application/json' } });
        return Response.json(reportFixture(['foreign']));
      },
    });
    await selectScope(user);
    await user.click(checkbox());
    await user.click(run());
    await screen.findByRole('alert');
    expect(results()).not.toBeInTheDocument();
    expect(posts).toHaveLength(1);
    expect(checkbox()).toBeChecked();
    expect(document.body.textContent).not.toContain('PRIVATE');
    if (kind === 'disabled')
      expect(screen.getByRole('alert')).toHaveTextContent('CAPABILITY_REPORT_API_DISABLED');
  },
);
test.each(['analysis', 'snapshot', 'manual-hash', 'findings'] as const)(
  '%s selection change clears the previous report immediately',
  async (change) => {
    const { user, posts } = setup();
    await selectScope(user);
    await user.click(checkbox());
    await user.click(run());
    await screen.findByRole('region', { name: 'Capability matching report' });
    if (change === 'analysis')
      await user.selectOptions(screen.getByRole('combobox', { name: 'Analysis' }), 'analysis-2');
    if (change === 'snapshot')
      await user.selectOptions(
        screen.getByRole('combobox', { name: 'Imported snapshot' }),
        otherHash,
      );
    if (change === 'manual-hash')
      await user.clear(screen.getByRole('textbox', { name: 'Exact snapshot hash' }));
    if (change === 'findings') await user.click(checkbox('finding-2'));
    expect(results()).not.toBeInTheDocument();
    expect(posts).toHaveLength(1);
  },
);
test('duplicate clicks cannot overlap; canceled late responses never display under a changed selection', async () => {
  let finish!: (response: Response) => void;
  const pending = new Promise<Response>((resolve) => {
    finish = resolve;
  });
  const { user, posts, requests } = setup({ report: () => pending });
  await selectScope(user);
  await user.click(checkbox());
  fireEvent.click(run());
  fireEvent.click(run());
  await waitFor(() => expect(posts).toHaveLength(1));
  expect(screen.getByRole('status')).toHaveTextContent('Computing capability report');
  await user.click(checkbox('finding-2'));
  expect(requests.find((r) => r.method === 'POST')!.signal.aborted).toBe(true);
  await act(async () => finish(Response.json(reportFixture())));
  expect(results()).not.toBeInTheDocument();
  expect(posts).toHaveLength(1);
});
test('private reports never enter query caches, browser storage or application logs', async () => {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const store = vi.spyOn(Storage.prototype, 'setItem');
  const log = vi.spyOn(console, 'log');
  const { user } = setup({ client });
  await selectScope(user);
  await user.click(checkbox());
  await user.click(run());
  await screen.findByRole('region', { name: 'Capability matching report' });
  expect(
    JSON.stringify(
      client
        .getQueryCache()
        .getAll()
        .map((q) => q.state.data),
    ),
  ).not.toContain('person_id');
  expect(client.getMutationCache().getAll()).toHaveLength(0);
  expect(store).not.toHaveBeenCalled();
  expect(log).not.toHaveBeenCalled();
});
test('navigation drops report state and does not rerun a report on returning', async () => {
  const { user, posts } = setup();
  await selectScope(user);
  await user.click(checkbox());
  await user.click(run());
  await screen.findByRole('region', { name: 'Capability matching report' });
  await user.click(screen.getByRole('link', { name: 'Skill Mapping' }));
  expect(results()).not.toBeInTheDocument();
  await user.click(screen.getByRole('link', { name: 'Capability Matching' }));
  expect(results()).not.toBeInTheDocument();
  expect(screen.getByText(/No requirements selected/)).toBeInTheDocument();
  expect(posts).toHaveLength(1);
});
test('unconfigured backend performs no discovery or report requests', () => {
  const { fetcher } = setup({ configured: false });
  expect(screen.getByText(/TenderCite is not configured/)).toBeInTheDocument();
  expect(fetcher).not.toHaveBeenCalled();
});
test('cross-analysis discovery data is rejected instead of silently substituted', async () => {
  const { user, posts } = setup({ findings: () => Response.json([inventoryFinding('foreign')]) });
  await user.selectOptions(await screen.findByRole('combobox', { name: 'Analysis' }), 'analysis-1');
  expect(await screen.findByRole('alert')).toHaveTextContent('mismatched finding');
  expect(run()).toBeDisabled();
  expect(posts).toHaveLength(0);
});
