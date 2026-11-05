import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, expect, test, vi } from 'vitest';
import { App } from '../App';
import type { BkmsCollection } from '../api/bkmsCatalogs';
import { deadlineEvidence, deadlineSummary } from '../test/bkmsDeadlines';
import {
  bkmsCatalog,
  bkmsMultiResults as bkmsResults,
  bkmsCollection,
  bkmsRecord,
  bkmsNotice,
} from '../test/bkms';

afterEach(() => {
  vi.unstubAllGlobals();
  window.history.replaceState({}, '', '/');
});
function setup(
  options: {
    configured?: boolean;
    loaded?: boolean;
    collection?: BkmsCollection;
    catalogRead?: () => Response | Promise<Response>;
    save?: () => Response | Promise<Response>;
    load?: () => Response | Promise<Response>;
    results?: unknown;
    deadlineLoad?: () => Response | Promise<Response>;
  } = {},
) {
  const posts: { path: string; body: unknown }[] = [];
  let loaded = options.loaded ?? false;
  let current = options.collection;
  let reads = 0;
  const fetcher = vi.fn(async (request: Request) => {
    const path = new URL(request.url).pathname;
    expect(new URL(request.url).origin).toBe(window.location.origin);
    if (request.method === 'GET' && path.includes('/discovery/bkms/deadlines/'))
      return Response.json(deadlineSummary());
    if (
      request.method === 'GET' &&
      path.endsWith('/discovery/bkms/catalogs') &&
      options.catalogRead &&
      reads++ === 0
    )
      return options.catalogRead();
    if (request.method === 'GET' && path.endsWith('/discovery/bkms/catalogs'))
      return Response.json(current ?? bkmsCollection(!loaded));
    if (request.method === 'POST') {
      posts.push({ path, body: await request.json() });
      if (path.endsWith('/deadlines/load'))
        return options.deadlineLoad ? options.deadlineLoad() : Response.json(deadlineSummary());
      if (path.endsWith('/load')) {
        loaded = true;
        return options.load ? options.load() : Response.json(bkmsCatalog());
      }
      if (path.endsWith('/search')) return Response.json(options.results ?? bkmsResults());
      if (path.endsWith('/remove')) {
        const body = posts.at(-1)!.body as { export_day: string };
        const previous = current ?? bkmsCollection(!loaded);
        current = {
          ...previous,
          collection_id: 'f'.repeat(64),
          days: previous.days.filter((d) => d.catalog.export_day !== body.export_day),
          stored_bytes: 0,
        };
        return Response.json(current);
      }
      if (path.endsWith('/import'))
        return options.save
          ? options.save()
          : Response.json({
              schema_version: 'native-opportunity-save-result-1',
              status: 'CREATED',
              record: bkmsRecord(),
            });
    }
    if (request.method === 'GET' && path.includes('/opportunities/'))
      return Response.json(bkmsRecord());
    if (request.method === 'GET' && path.endsWith('/opportunities'))
      return Response.json({
        schema_version: 'native-opportunity-listing-1',
        opportunities: [],
        next_offset: null,
      });
    throw new Error('Unexpected synthetic request');
  });
  vi.stubGlobal('fetch', fetcher);
  vi.stubGlobal('__TENDERCITE_CONFIGURED__', options.configured ?? true);
  window.history.replaceState({}, '', '/discover');
  render(<App />);
  return { posts, fetcher };
}
async function choose() {
  await userEvent.selectOptions(screen.getByLabelText('Discovery source'), 'BKMS');
  await screen.findByRole('heading', { name: 'German Bekanntmachungsservice — Open Data' });
}
async function search() {
  await waitFor(() =>
    expect(screen.getByRole('button', { name: 'Search local notices' })).toBeEnabled(),
  );
  await userEvent.type(screen.getByLabelText('Keyword phrase'), 'Software');
  await userEvent.click(screen.getByRole('button', { name: 'Search local notices' }));
  await screen.findByRole('heading', { name: 'Synthetic software procurement' });
}
test('switching sources does not search or download; daily load and search are separate explicit actions', async () => {
  const { posts } = setup();
  expect(posts).toEqual([]);
  await choose();
  await screen.findByText(/No daily catalog loaded/);
  expect(posts).toEqual([]);
  fireEvent.change(screen.getByLabelText('Publication day (Europe/Berlin)'), {
    target: { value: '2026-10-30' },
  });
  expect(posts).toEqual([]);
  await userEvent.click(screen.getByRole('button', { name: 'Load publication day' }));
  await screen.findByLabelText('Loaded German catalog');
  expect(posts).toEqual([
    {
      path: '/tendercite-api/api/v1/discovery/bkms/load',
      body: { schema_version: 'bkms-load-input-1', export_day: '2026-10-30' },
    },
  ]);
  await search();
  expect(posts).toHaveLength(2);
  expect(posts[1]!.body).toEqual({
    schema_version: 'bkms-search-input-2',
    collection_id: 'd'.repeat(64),
    keyword: 'Software',
    page: 1,
    limit: 10,
    notice_category: 'ALL',
    include_earlier_versions: false,
  });
  expect(screen.getByRole('link', { name: 'Original German publication' })).toHaveAttribute(
    'href',
    'https://oeffentlichevergabe.de/ui/notices/10000001?noticeVersion=1',
  );
  await userEvent.selectOptions(screen.getByLabelText('Discovery source'), 'TED');
  expect(screen.getByRole('button', { name: 'Search' })).toBeVisible();
  expect(posts).toHaveLength(2);
});
test('explicit save, native editor and downstream links retain source attribution', async () => {
  const { posts } = setup({ loaded: true });
  await choose();
  await search();
  expect(posts.filter((p) => p.path.endsWith('/import'))).toHaveLength(0);
  await userEvent.click(screen.getByRole('button', { name: 'Save to Opportunities' }));
  const saved = await screen.findByLabelText(`Saved German publication ${bkmsNotice}`);
  expect(saved).toHaveTextContent('Server confirmed CREATED');
  expect(posts[1]!.body).toEqual({
    schema_version: 'bkms-import-input-1',
    catalog_id: 'a'.repeat(64),
    external_id: bkmsNotice,
  });
  expect(within(saved).getByRole('link', { name: 'Compare Skills' })).toHaveAttribute(
    'href',
    expect.stringContaining('/matching?opportunity_id='),
  );
  expect(within(saved).getByRole('link', { name: 'Add to Pipeline' })).toHaveAttribute(
    'href',
    expect.stringContaining('/pipeline?opportunity_id='),
  );
  await userEvent.click(within(saved).getByRole('link', { name: 'View Opportunity' }));
  expect(await screen.findByLabelText('German procurement import provenance')).toHaveTextContent(
    'Bekanntmachungsservice',
  );
  await userEvent.click(screen.getByText('Original Open Data metadata'));
  expect(screen.getByText(/Original title: Synthetic software procurement/)).toBeVisible();
});
test('already-present result does not overwrite edited opportunity', async () => {
  const record = bkmsRecord();
  record.revision = 2;
  record.opportunity.title = 'My local edit';
  setup({
    loaded: true,
    save: () =>
      Response.json({
        schema_version: 'native-opportunity-save-result-1',
        status: 'ALREADY_PRESENT',
        record,
      }),
  });
  await choose();
  await search();
  await userEvent.click(screen.getByRole('button', { name: 'Save to Opportunities' }));
  expect(await screen.findByText(/Server confirmed ALREADY_PRESENT/)).toHaveTextContent(
    'Existing local edits were retained',
  );
});
test('in-flight import prevents overlapping saves and source changes', async () => {
  const { posts } = setup({ loaded: true, save: () => new Promise(() => {}) });
  await choose();
  await search();
  const button = screen.getByRole('button', { name: 'Save to Opportunities' });
  fireEvent.click(button);
  fireEvent.click(button);
  await waitFor(() => expect(button).toBeDisabled());
  expect(screen.getByLabelText('Discovery source')).toBeDisabled();
  expect(posts.filter((p) => p.path.endsWith('/import'))).toHaveLength(1);
});
test('lost import response is uncertain, safe and never retried automatically', async () => {
  const { posts } = setup({
    loaded: true,
    save: () => Promise.reject(new Error('PRIVATE raw records')),
  });
  await choose();
  await search();
  await userEvent.click(screen.getByRole('button', { name: 'Save to Opportunities' }));
  const error = await screen.findByRole('alert');
  expect(error).toHaveTextContent('an opportunity may already exist');
  expect(error).not.toHaveTextContent('PRIVATE');
  expect(posts.filter((p) => p.path.endsWith('/import'))).toHaveLength(1);
  expect(screen.getByRole('heading', { name: 'Synthetic software procurement' })).toBeVisible();
});
test('catalog conflict and missing metadata remain explicit, without automatic retry', async () => {
  const { posts } = setup({
    loaded: true,
    save: () =>
      Response.json(
        { detail: { code: 'BKMS_CATALOG_CONFLICT', message: 'PRIVATE' } },
        { status: 409 },
      ),
  });
  await choose();
  await search();
  await userEvent.click(screen.getByRole('button', { name: 'Save to Opportunities' }));
  expect(await screen.findByRole('alert')).toHaveTextContent('BKMS_CATALOG_CONFLICT');
  expect(posts).toHaveLength(2);
});
test('failed download can be inspected through local catalog read, with no second provider load', async () => {
  const { posts } = setup({ load: () => Promise.reject(new Error('PRIVATE')) });
  await choose();
  fireEvent.change(screen.getByLabelText('Publication day (Europe/Berlin)'), {
    target: { value: '2026-10-30' },
  });
  await userEvent.click(screen.getByRole('button', { name: 'Load publication day' }));
  expect(await screen.findByRole('alert')).toHaveTextContent(
    'Download/storage outcome is not confirmed',
  );
  await userEvent.click(screen.getByRole('button', { name: 'Read current local catalog' }));
  await screen.findByLabelText('Loaded German catalog');
  expect(posts).toHaveLength(1);
});
test('missing title prevents saving', async () => {
  const results = bkmsResults();
  results.items[0]!.source.original_metadata.title = null;
  results.items[0]!.source.mapping_warnings = ['MISSING_TITLE'];
  setup({ loaded: true, results });
  await choose();
  await screen.findByLabelText('Loaded German catalog');
  await userEvent.type(screen.getByLabelText('Keyword phrase'), 'Software');
  await userEvent.click(screen.getByRole('button', { name: 'Search local notices' }));
  expect(await screen.findByRole('heading', { name: 'Title not supplied' })).toBeVisible();
  expect(screen.getByRole('button', { name: 'Save to Opportunities' })).toBeDisabled();
});

test('empty results are explicitly scoped to the loaded catalog', async () => {
  setup({ loaded: true, results: { ...bkmsResults(), items: [], total_count: 0 } });
  await choose();
  await screen.findByLabelText('Loaded German catalog');
  await userEvent.type(screen.getByLabelText('Keyword phrase'), 'Software');
  await userEvent.click(screen.getByRole('button', { name: 'Search local notices' }));
  expect(await screen.findByText(/No local notices match this keyword and view/)).toBeVisible();
});

test('unconfigured proxy makes no requests for either source', async () => {
  const { fetcher } = setup({ configured: false });
  await userEvent.selectOptions(screen.getByLabelText('Discovery source'), 'BKMS');
  expect(screen.getByText(/TenderCite is not configured/)).toBeVisible();
  expect(fetcher).not.toHaveBeenCalled();
});

test('delayed initial metadata cannot replace a successfully loaded catalog', async () => {
  let complete: (response: Response) => void = () => {
    throw new Error('Read not started');
  };
  const { posts } = setup({
    catalogRead: () =>
      new Promise((resolve) => {
        complete = resolve;
      }),
  });
  await choose();
  fireEvent.change(screen.getByLabelText('Publication day (Europe/Berlin)'), {
    target: { value: '2026-10-30' },
  });
  await userEvent.click(screen.getByRole('button', { name: 'Load publication day' }));
  await screen.findByLabelText('Loaded German catalog');
  await act(async () => complete(Response.json(bkmsCollection(true))));
  expect(screen.getByLabelText('Loaded German catalog')).toHaveTextContent('2026-10-30');
  expect(posts).toHaveLength(1);
});

test('replacing a retained day requires a separate confirmation with the current hash', async () => {
  const { posts } = setup({ loaded: true });
  await choose();
  await waitFor(() =>
    expect(screen.getByRole('button', { name: 'Search local notices' })).toBeEnabled(),
  );
  fireEvent.change(screen.getByLabelText('Publication day (Europe/Berlin)'), {
    target: { value: '2026-10-30' },
  });
  expect(screen.getByRole('button', { name: 'Load publication day' })).toBeDisabled();
  await userEvent.click(screen.getByRole('button', { name: 'Replace stored day' }));
  expect(posts).toEqual([]);
  await userEvent.click(screen.getByRole('button', { name: 'Cancel catalog change' }));
  expect(posts).toEqual([]);
  await userEvent.click(screen.getByRole('button', { name: 'Replace stored day' }));
  await userEvent.click(screen.getByRole('button', { name: 'Confirm day replacement' }));
  await screen.findByText(/Server confirmed replacement/);
  expect(posts).toEqual([
    {
      path: '/tendercite-api/api/v1/discovery/bkms/load',
      body: {
        schema_version: 'bkms-load-input-1',
        export_day: '2026-10-30',
        expected_catalog_id: 'a'.repeat(64),
      },
    },
  ]);
});

test('removing a day is explicit and refreshes coverage without a provider download', async () => {
  const { posts } = setup({ loaded: true });
  await choose();
  const remove = await screen.findByRole('button', { name: 'Remove 2026-10-30' });
  await userEvent.click(remove);
  expect(posts).toEqual([]);
  expect(screen.getByRole('alertdialog', { name: 'Confirm catalog change' })).toHaveTextContent(
    'Imported opportunities',
  );
  await userEvent.click(screen.getByRole('button', { name: 'Confirm day removal' }));
  await screen.findByText(/Server confirmed removal/);
  expect(posts).toEqual([
    {
      path: '/tendercite-api/api/v1/discovery/bkms/remove',
      body: {
        schema_version: 'bkms-remove-input-1',
        export_day: '2026-10-30',
        expected_catalog_id: 'a'.repeat(64),
      },
    },
  ]);
  expect(screen.getByRole('button', { name: 'Search local notices' })).toBeDisabled();
  expect(screen.getByText(/No stored days/)).toBeVisible();
});

test('seven retained days prevent adding another without automatically removing anything', async () => {
  const collection = bkmsCollection();
  collection.days = Array.from({ length: 7 }, (_, i) => ({
    catalog: {
      ...bkmsCatalog(),
      export_day: `2026-10-${30 - i}`,
      catalog_id: String(i + 1).repeat(64),
    },
    warning_counts: {},
  }));
  const { posts } = setup({ collection });
  await choose();
  await screen.findByText(/Stored publication days \(7 \/ 7\)/);
  fireEvent.change(screen.getByLabelText('Publication day (Europe/Berlin)'), {
    target: { value: '2026-10-31' },
  });
  expect(screen.getByRole('button', { name: 'Load publication day' })).toBeDisabled();
  expect(screen.getByText(/no day is deleted automatically/)).toBeVisible();
  expect(posts).toEqual([]);
});

test('notice-type and earlier-version controls only apply on explicit search', async () => {
  const result = bkmsResults();
  result.notice_category = 'AWARD';
  result.include_earlier_versions = true;
  result.items[0]!.notice_category = 'AWARD';
  result.items[0]!.is_latest_stored = false;
  result.items[0]!.version_count = 2;
  result.items[0]!.source.original_metadata.tags = ['award'];
  result.coverage.days[0]!.catalog.record_count = 2;
  const { posts } = setup({ loaded: true, results: result });
  await choose();
  await waitFor(() =>
    expect(screen.getByRole('button', { name: 'Search local notices' })).toBeEnabled(),
  );
  await userEvent.selectOptions(screen.getByLabelText('Notice type'), 'AWARD');
  await userEvent.click(screen.getByLabelText('Show all stored notice versions'));
  expect(posts).toEqual([]);
  await userEvent.type(screen.getByLabelText('Keyword phrase'), 'Software');
  await userEvent.click(screen.getByRole('button', { name: 'Search local notices' }));
  await screen.findByRole('heading', { name: 'Synthetic software procurement' });
  expect(posts[0]!.body).toEqual({
    schema_version: 'bkms-search-input-2',
    collection_id: 'd'.repeat(64),
    keyword: 'Software',
    page: 1,
    limit: 10,
    notice_category: 'AWARD',
    include_earlier_versions: true,
  });
  expect(screen.getByText(/Earlier stored numeric version/)).toBeVisible();
});

test('missing tags remain visibly unknown', async () => {
  const result = bkmsResults();
  result.items[0]!.source.original_metadata.tags = [];
  result.items[0]!.notice_category = 'OTHER_UNKNOWN';
  setup({ loaded: true, results: result });
  await choose();
  await search();
  expect(
    screen.getByText(/Notice type: Other \/ unknown. Source tags: Unknown \/ not supplied/),
  ).toBeVisible();
});

test('official deadline download is explicit, bound to the selected day and never adopts opportunity fields', async () => {
  const { posts } = setup({ loaded: true });
  await choose();
  const button = await screen.findByRole('button', {
    name: 'Load official deadlines for 2026-10-30',
  });
  expect(posts).toHaveLength(0);
  await userEvent.click(button);
  await screen.findByText(/Server confirmed official deadline enrichment/);
  expect(posts).toEqual([
    {
      path: '/tendercite-api/api/v1/discovery/bkms/deadlines/load',
      body: {
        schema_version: 'bkms-deadline-load-1',
        catalog_id: 'a'.repeat(64),
        expected_enrichment_id: '9'.repeat(64),
      },
    },
  ]);
  expect(screen.getByText(/Stored deadline state: CURRENT/)).toBeVisible();
  expect(screen.queryByRole('button', { name: /Adopt|Apply deadline/ })).not.toBeInTheDocument();
});

test.each(['MULTIPLE_LOTS', 'CONFLICT', 'UNRECOGNIZED'] as const)(
  'stored %s source values stay distinct from a local deadline',
  async (status) => {
    const e = deadlineEvidence();
    e.notice.status = status;
    e.notice.lots.push({
      lot_id: 'LOT-0002',
      status: 'SINGLE',
      periods: [{ dates: ['2026-12-09+01:00'], times: ['11:00:00+01:00'] }],
    });
    const { posts } = setup({
      loaded: true,
      results: {
        ...bkmsResults(),
        items: [{ ...bkmsResults().items[0]!, deadline_enrichment: e }],
      },
    });
    await choose();
    await search();
    const panel = screen.getByRole('complementary', {
      name: 'Official eForms submission deadlines',
    });
    expect(panel).toHaveTextContent(status);
    expect(panel).toHaveTextContent('2026-12-08+01:00');
    expect(panel).toHaveTextContent('2026-12-09+01:00');
    expect(panel).toHaveTextContent('future deadline does not prove bidding is open');
    expect(panel).toHaveTextContent('Saving a notice does not adopt these values');
    expect(posts.filter((p) => p.path.includes('/deadlines/'))).toHaveLength(0);
  },
);

test('pending deadline download prevents overlapping mutations', async () => {
  let finish!: (r: Response) => void;
  const { posts } = setup({
    loaded: true,
    deadlineLoad: () =>
      new Promise((r) => {
        finish = r;
      }),
  });
  await choose();
  const button = await screen.findByRole('button', {
    name: 'Load official deadlines for 2026-10-30',
  });
  await userEvent.click(button);
  await waitFor(() => expect(finish).toBeDefined());
  fireEvent.click(button);
  expect(button).toBeDisabled();
  expect(screen.getByLabelText('Discovery source')).toBeDisabled();
  expect(posts).toHaveLength(1);
  await act(async () => finish(Response.json(deadlineSummary())));
  await screen.findByText(/Server confirmed official deadline enrichment/);
});

test('uncertain enrichment reports safely, preserves selected controls, and never retries', async () => {
  const { posts } = setup({
    loaded: true,
    deadlineLoad: () => Promise.reject(new Error('PRIVATE')),
  });
  await choose();
  await userEvent.type(screen.getByLabelText('Keyword phrase'), 'Software');
  await userEvent.click(
    await screen.findByRole('button', { name: 'Load official deadlines for 2026-10-30' }),
  );
  await screen.findByText(/Enrichment persistence is not confirmed/);
  expect(screen.getByRole('alert')).not.toHaveTextContent('PRIVATE');
  expect(screen.getByLabelText('Keyword phrase')).toHaveValue('Software');
  expect(
    screen.queryByText(/Server confirmed official deadline enrichment/),
  ).not.toBeInTheDocument();
  expect(posts).toHaveLength(1);
});
