import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, expect, test, vi } from 'vitest';
import { App } from '../App';
import { importedId, tedId, tedRecord, tedResults } from '../test/discovery';
import type { TedSearchResult } from '../api/discovery';

afterEach(() => {
  vi.unstubAllGlobals();
  window.history.replaceState({}, '', '/');
});
function setup(
  options: {
    configured?: boolean;
    result?: TedSearchResult;
    search?: (body: Record<string, unknown>) => Response | Promise<Response>;
    save?: () => Response | Promise<Response>;
  } = {},
) {
  const searches: Record<string, unknown>[] = [];
  const imports: Record<string, unknown>[] = [];
  const fetcher = vi.fn(async (request: Request) => {
    expect(new URL(request.url).origin).toBe(window.location.origin);
    if (request.url.includes('/discovery/ted/')) {
      expect(request.method).toBe('POST');
      const body = await request.json();
      if (request.url.endsWith('/search')) {
        searches.push(body);
        return options.search
          ? options.search(body)
          : Response.json({ ...(options.result ?? tedResults()), page: body.page });
      }
      imports.push(body);
      return options.save
        ? options.save()
        : Response.json({
            schema_version: 'native-opportunity-save-result-1',
            status: 'CREATED',
            record: tedRecord(),
          });
    }
    expect(request.method).toBe('GET');
    if (request.url.includes('/opportunities/')) return Response.json(tedRecord());
    if (new URL(request.url).pathname.endsWith('/opportunities'))
      return Response.json({
        schema_version: 'native-opportunity-listing-1',
        opportunities: [],
        next_offset: null,
      });
    if (new URL(request.url).pathname.endsWith('/profiles'))
      return Response.json({
        schema_version: 'native-profile-listing-1',
        profiles: [],
        next_offset: null,
      });
    if (request.url.includes('/pipeline'))
      return Response.json({
        schema_version: 'native-pipeline-listing-1',
        entries: [],
        next_offset: null,
      });
    throw new Error('Unexpected synthetic API request');
  });
  vi.stubGlobal('fetch', fetcher);
  vi.stubGlobal('__TENDERCITE_CONFIGURED__', options.configured ?? true);
  window.history.replaceState({}, '', '/discover');
  render(<App />);
  return { user: userEvent.setup(), fetcher, searches, imports };
}
async function search(user: ReturnType<typeof userEvent.setup>) {
  await user.type(screen.getByLabelText('Keyword phrase'), 'Software');
  await user.click(screen.getByRole('button', { name: 'Search' }));
  await screen.findByRole('region', { name: 'TED search results' });
}
test('no automatic search/import on load or keystrokes; explicit search shows safe official metadata', async () => {
  const { user, fetcher, searches, imports } = setup();
  expect(fetcher).not.toHaveBeenCalled();
  await user.type(screen.getByLabelText('Keyword phrase'), 'Software');
  expect(fetcher).not.toHaveBeenCalled();
  await user.click(screen.getByRole('button', { name: 'Search' }));
  expect(await screen.findByText('Synthetic software services')).toBeVisible();
  expect(screen.getByText('Organization: Fictional public buyer')).toBeVisible();
  expect(searches).toEqual([
    { schema_version: 'ted-search-input-1', keyword: 'Software', page: 1, limit: 10 },
  ]);
  expect(imports).toHaveLength(0);
  const original = screen.getByRole('link', { name: 'Original TED publication' });
  expect(original).toHaveAttribute('href', `https://ted.europa.eu/de/notice/-/detail/${tedId}`);
  expect(original).toHaveAttribute('rel', 'noopener noreferrer');
  await user.click(screen.getByText('Inspect TED result'));
  expect(screen.getByText('Publication date: 2099-02-01')).toBeVisible();
});
test('explicit import sends publication identity only and offers existing native workflow links', async () => {
  const { user, imports } = setup();
  await search(user);
  await user.click(screen.getByRole('button', { name: 'Save to Opportunities' }));
  expect(await screen.findByText(/Server confirmed CREATED/)).toBeVisible();
  expect(imports).toEqual([{ schema_version: 'ted-import-input-1', publication_id: tedId }]);
  expect(screen.getByRole('link', { name: 'View Opportunity' })).toHaveAttribute(
    'href',
    `/opportunities?opportunity_id=${importedId}`,
  );
  expect(screen.getByRole('link', { name: 'Compare Skills' })).toHaveAttribute(
    'href',
    `/matching?opportunity_id=${importedId}`,
  );
  expect(screen.getByRole('link', { name: 'Add to Pipeline' })).toHaveAttribute(
    'href',
    `/pipeline?opportunity_id=${importedId}`,
  );
  expect(screen.queryByRole('button', { name: 'Save to Opportunities' })).not.toBeInTheDocument();
  expect(localStorage.length + sessionStorage.length).toBe(0);
});
test('pagination uses the submitted keyword and limit rather than an unsent draft', async () => {
  const result = tedResults();
  result.total_notice_count = 21;
  result.next_page = 2;
  const { user, searches, imports } = setup({ result });
  await search(user);
  await user.clear(screen.getByLabelText('Keyword phrase'));
  await user.type(screen.getByLabelText('Keyword phrase'), 'Other draft');
  await user.click(screen.getByRole('button', { name: 'Next results' }));
  await waitFor(() => expect(searches).toHaveLength(2));
  expect(searches[1]).toMatchObject({ keyword: 'Software', page: 2, limit: 10 });
  expect(imports).toHaveLength(0);
});
test('missing metadata and empty result sets stay explicit, never fabricated', async () => {
  const result = tedResults();
  result.items[0]!.title = null;
  result.items[0]!.organization = null;
  result.items[0]!.summary = null;
  result.items[0]!.importable = false;
  result.items[0]!.warnings = ['MISSING_TITLE'];
  const { user, imports } = setup({ result });
  await search(user);
  expect(screen.getByText('Title not supplied by TED')).toBeVisible();
  expect(screen.getByRole('button', { name: 'Save to Opportunities' })).toBeDisabled();
  expect(imports).toHaveLength(0);
});
test('valid empty search is distinct from provider error', async () => {
  const result = tedResults();
  result.items = [];
  result.total_notice_count = 0;
  const { user } = setup({ result });
  await search(user);
  expect(screen.getByText('TED returned no results for this keyword.')).toBeVisible();
});
test.each(['disabled', 'rate', 'malformed', 'network'])(
  'safe %s search error has no automatic retry',
  async (kind) => {
    const { user, searches } = setup({
      search: () => {
        if (kind === 'network') throw new Error('PRIVATE');
        if (kind === 'malformed') return Response.json({ PRIVATE: 'PRIVATE' });
        return Response.json(
          {
            detail: {
              code: kind === 'disabled' ? 'DISCOVERY_API_DISABLED' : 'TED_RATE_LIMITED',
              message: 'PRIVATE',
            },
          },
          { status: 429 },
        );
      },
    });
    await user.type(screen.getByLabelText('Keyword phrase'), 'Software');
    await user.click(screen.getByRole('button', { name: 'Search' }));
    expect(await screen.findByRole('alert')).not.toHaveTextContent('PRIVATE');
    expect(searches).toHaveLength(1);
    expect(screen.queryByText('TED returned no results')).not.toBeInTheDocument();
  },
);
test.each(['network', 'http', 'malformed'])(
  'uncertain %s import is not claimed as saved and never retries',
  async (kind) => {
    const { user, imports } = setup({
      save: () => {
        if (kind === 'network') throw new Error('PRIVATE');
        return kind === 'http'
          ? Response.json({ PRIVATE: 'PRIVATE' }, { status: 503 })
          : Response.json({ PRIVATE: 'PRIVATE' });
      },
    });
    await search(user);
    await user.click(screen.getByRole('button', { name: 'Save to Opportunities' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('Save not confirmed');
    expect(screen.getByRole('alert')).not.toHaveTextContent('PRIVATE');
    expect(screen.queryByText(/Server confirmed CREATED/)).not.toBeInTheDocument();
    expect(imports).toHaveLength(1);
  },
);
test('ALREADY_PRESENT retains local edits and gives navigation to the existing record', async () => {
  const record = tedRecord();
  record.revision = 4;
  record.opportunity.title = 'Local edited title';
  record.opportunity.required_skills = ['Python'];
  const { user } = setup({
    save: () =>
      Response.json({
        schema_version: 'native-opportunity-save-result-1',
        status: 'ALREADY_PRESENT',
        record,
      }),
  });
  await search(user);
  await user.click(screen.getByRole('button', { name: 'Save to Opportunities' }));
  expect(await screen.findByText(/Server confirmed ALREADY_PRESENT/)).toHaveTextContent(
    'local edits were retained',
  );
});
test('imported opportunity opens in the existing editor with provenance and can add manual skills', async () => {
  const { user, imports } = setup();
  await search(user);
  await user.click(screen.getByRole('button', { name: 'Save to Opportunities' }));
  await user.click(await screen.findByRole('link', { name: 'View Opportunity' }));
  const evidence = await screen.findByRole('complementary', { name: 'TED import provenance' });
  expect(evidence).toHaveTextContent(tedId);
  expect(screen.getByLabelText('Opportunity title')).toHaveValue('Synthetic software services');
  await user.click(screen.getByRole('button', { name: 'Add required skill' }));
  expect(screen.getByLabelText('Skill 1 name')).toHaveValue('');
  expect(imports).toHaveLength(1);
});
test('overlapping import clicks are prevented and pending navigation is guarded', async () => {
  let complete!: (response: Response) => void;
  const { user, imports } = setup({
    save: () =>
      new Promise((resolve) => {
        complete = resolve;
      }),
  });
  await search(user);
  const save = screen.getByRole('button', { name: 'Save to Opportunities' });
  fireEvent.click(save);
  fireEvent.click(save);
  await waitFor(() => expect(imports).toHaveLength(1));
  expect(save).toBeDisabled();
  await user.click(
    within(screen.getByRole('navigation', { name: 'Primary navigation' })).getByRole('link', {
      name: 'Dashboard',
    }),
  );
  expect(await screen.findByRole('alertdialog', { name: 'TED import in progress' })).toBeVisible();
  await user.click(screen.getByRole('button', { name: 'Stay on Discover' }));
  await act(async () =>
    complete(
      Response.json({
        schema_version: 'native-opportunity-save-result-1',
        status: 'CREATED',
        record: tedRecord(),
      }),
    ),
  );
  await screen.findByText(/Server confirmed CREATED/);
});
test('unconfigured backend renders a clear state without any requests', () => {
  const { fetcher } = setup({ configured: false });
  expect(screen.getByText(/TenderCite is not configured/)).toBeVisible();
  expect(fetcher).not.toHaveBeenCalled();
});
