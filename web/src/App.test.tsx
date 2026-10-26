import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, expect, test, vi } from 'vitest';
import { App } from './App';
import { makeQueryClient } from './api/queryClient';

afterEach(() => {
  vi.unstubAllGlobals();
  window.history.replaceState({}, '', '/');
});
test('standalone root redirects to tenders and only reads from the TenderCite proxy', async () => {
  vi.stubGlobal('__TENDERCITE_CONFIGURED__', true);
  const fetcher = vi.fn(async (request: Request) => {
    expect(new URL(request.url).origin).toBe(window.location.origin);
    expect(new URL(request.url).pathname).toMatch(
      /^\/tendercite-api\/api\/v1\/(documents|findings)$/,
    );
    return Response.json([]);
  });
  vi.stubGlobal('fetch', fetcher);
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
