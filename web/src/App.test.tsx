import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, expect, test, vi } from 'vitest';
import { App } from './App';
import { makeQueryClient } from './api/queryClient';

afterEach(() => {
  vi.unstubAllGlobals();
  window.history.replaceState({}, '', '/');
});
test('standalone root redirects to dashboard and only reads bounded native previews', async () => {
  vi.stubGlobal('__TENDERCITE_CONFIGURED__', true);
  const fetcher = vi.fn(async (request: Request) => {
    expect(new URL(request.url).origin).toBe(window.location.origin);
    expect(request.method).toBe('GET');
    const resource = new URL(request.url).pathname.split('/').at(-1);
    if (resource === 'pipeline')
      return Response.json({
        schema_version: 'native-pipeline-listing-1',
        entries: [],
        next_offset: null,
      });
    return Response.json({
      schema_version:
        resource === 'profiles' ? 'native-profile-listing-1' : 'native-opportunity-listing-1',
      [resource!]: [],
      next_offset: null,
    });
  });
  vi.stubGlobal('fetch', fetcher);
  render(<App />);
  await screen.findByText(/No saved profiles in this preview/);
  await screen.findByText(/No tracked opportunities in this preview/);
  expect(window.location.pathname).toBe('/dashboard');
  expect(fetcher).toHaveBeenCalledTimes(3);
});
test('existing tenders deep link still reads documents and findings', async () => {
  vi.stubGlobal('__TENDERCITE_CONFIGURED__', true);
  const fetcher = vi.fn(async (request: Request) => {
    expect(new URL(request.url).origin).toBe(window.location.origin);
    expect(new URL(request.url).pathname).toMatch(
      /^\/tendercite-api\/api\/v1\/(documents|findings)$/,
    );
    return Response.json([]);
  });
  vi.stubGlobal('fetch', fetcher);
  window.history.replaceState({}, '', '/tenders');
  render(<App />);
  await screen.findByText('No documents available.');
  await screen.findByText('No findings available.');
  expect(window.location.pathname).toBe('/tenders');
  expect(fetcher).toHaveBeenCalledTimes(2);
  expect(screen.getByRole('link', { name: 'Skip to content' })).toHaveAttribute('href', '#main');
  expect(screen.getByRole('main')).toHaveAttribute('id', 'main');
  expect(screen.getByRole('complementary', { name: 'Development environment' })).toHaveTextContent(
    'No authentication',
  );
  expect(screen.queryByRole('region', { name: 'Planning period' })).not.toBeInTheDocument();
});
test('independent navigation has no workforce, project, planning or snapshot export workflow', async () => {
  vi.stubGlobal('__TENDERCITE_CONFIGURED__', true);
  const fetcher = vi.fn();
  vi.stubGlobal('fetch', fetcher);
  window.history.replaceState({}, '', '/missing');
  render(<App />);
  const user = userEvent.setup();
  for (const name of [
    'Workforce',
    'Projects',
    'Capacity Planning',
    'Capability Gaps',
    'Workforce Snapshot',
  ])
    expect(screen.queryByRole('link', { name })).not.toBeInTheDocument();
  await user.click(screen.getByRole('link', { name: 'Import snapshot' }));
  expect(screen.getByText(/another compatible schema 1.0 producer/)).toBeInTheDocument();
  expect(fetcher).not.toHaveBeenCalled();
});
test('query client has no automatic mutation retries or workforce error/schema dependencies', () => {
  const client = makeQueryClient();
  expect(client.getDefaultOptions().queries?.retry).toBe(false);
  expect(client.getDefaultOptions().mutations?.retry).toBe(false);
});
