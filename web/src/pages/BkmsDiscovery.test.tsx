import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, expect, test, vi } from 'vitest';
import { App } from '../App';
import { bkmsCatalog, bkmsResults, bkmsRecord, bkmsNotice } from '../test/bkms';

afterEach(() => {
  vi.unstubAllGlobals();
  window.history.replaceState({}, '', '/');
});
function setup(
  options: {
    configured?: boolean;
    loaded?: boolean;
    save?: () => Response | Promise<Response>;
    load?: () => Response | Promise<Response>;
    results?: unknown;
  } = {},
) {
  const posts: { path: string; body: unknown }[] = [];
  let loaded = options.loaded ?? false;
  const fetcher = vi.fn(async (request: Request) => {
    const path = new URL(request.url).pathname;
    expect(new URL(request.url).origin).toBe(window.location.origin);
    if (request.method === 'GET' && path.endsWith('/discovery/bkms/catalog'))
      return loaded
        ? Response.json(bkmsCatalog())
        : Response.json({ detail: { code: 'BKMS_CATALOG_NOT_FOUND' } }, { status: 404 });
    if (request.method === 'POST') {
      posts.push({ path, body: await request.json() });
      if (path.endsWith('/load')) {
        loaded = true;
        return options.load ? options.load() : Response.json(bkmsCatalog());
      }
      if (path.endsWith('/search')) return Response.json(options.results ?? bkmsResults());
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
  await screen.findByLabelText('Loaded German catalog');
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
    schema_version: 'bkms-search-input-1',
    catalog_id: 'a'.repeat(64),
    keyword: 'Software',
    page: 1,
    limit: 10,
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
  expect(await screen.findByRole('alert')).toHaveTextContent('Catalog replacement is unconfirmed');
  await userEvent.click(screen.getByRole('button', { name: 'Read current local catalog' }));
  await screen.findByLabelText('Loaded German catalog');
  expect(posts).toHaveLength(1);
});
test('missing title prevents saving', async () => {
  const results = bkmsResults();
  results.items[0]!.original_metadata.title = null;
  results.items[0]!.mapping_warnings = ['MISSING_TITLE'];
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
  expect(await screen.findByText('No local notices match this keyword.')).toBeVisible();
});

test('unconfigured proxy makes no requests for either source', async () => {
  const { fetcher } = setup({ configured: false });
  await userEvent.selectOptions(screen.getByLabelText('Discovery source'), 'BKMS');
  expect(screen.getByText(/TenderCite is not configured/)).toBeVisible();
  expect(fetcher).not.toHaveBeenCalled();
});
