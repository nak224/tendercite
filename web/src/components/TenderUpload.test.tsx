import { act, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, expect, test, vi } from 'vitest';
import { App } from '../App';

import { syntheticPdf as pdf } from '../test/tenderPdf';

const document = {
  id: 'doc-1',
  filename: 'synthetic.pdf',
  sha256: 'a'.repeat(64),
  page_count: 1,
  chunk_count: 1,
  created_at: '2026-10-16',
};
afterEach(() => {
  vi.unstubAllGlobals();
  window.history.replaceState({}, '', '/');
});
function setup(
  upload: (request: Request) => Response | Promise<Response> = () => Response.json(document),
  configured = true,
  retainedOnFailure = false,
) {
  let documents: (typeof document)[] = [];
  const fetcher = vi.fn(async (request: Request) => {
    const url = new URL(request.url);
    expect(url.origin).toBe(window.location.origin);
    if (request.method === 'POST') {
      expect(url.pathname).toBe('/tendercite-api/api/v1/documents');
      const response = await upload(request);
      if (response.ok || retainedOnFailure) documents = [document];
      return response;
    }
    expect(request.method).toBe('GET');
    if (url.pathname.endsWith('/documents')) return Response.json(documents);
    if (url.pathname.endsWith('/findings')) return Response.json([]);
    throw Error('Unexpected request');
  });
  vi.stubGlobal('__TENDERCITE_CONFIGURED__', configured);
  vi.stubGlobal('fetch', fetcher);
  window.history.replaceState({}, '', '/tenders');
  const view = render(<App />);
  return {
    ...view,
    fetcher,
    user: userEvent.setup(),
    picker: () => screen.getByLabelText('PDF files'),
  };
}

test('selection alone sends no upload; explicit multipart upload refreshes documents', async () => {
  let sent!: Request;
  const { user, picker, fetcher } = setup((request) => {
    sent = request;
    return Response.json(document, { status: 201 });
  });
  expect(screen.getByRole('button', { name: 'Upload' })).toBeDisabled();
  await user.upload(picker(), pdf());
  expect(fetcher.mock.calls.filter(([request]) => request.method === 'POST')).toHaveLength(0);
  await user.click(screen.getByRole('button', { name: 'Upload' }));
  expect(await screen.findByRole('link', { name: 'Stored document doc-1' })).toBeInTheDocument();
  expect(await screen.findByRole('link', { name: 'synthetic.pdf' })).toBeInTheDocument();
  expect(screen.getByRole('status')).toHaveTextContent('1 of 1 files processed');
  expect(screen.getByText(/Indexing and search may be unavailable/)).toBeInTheDocument();
  expect(sent.credentials).toBe('omit');
  expect(sent.redirect).toBe('error');
  expect(sent.headers.get('content-type')).toContain('multipart/form-data; boundary=');
});

test('batch continues after individual failure and keeps successful and duplicate document results', async () => {
  let attempt = 0;
  const { user, picker, fetcher } = setup(() =>
    ++attempt === 2
      ? Response.json({ detail: 'PRIVATE' }, { status: 422 })
      : Response.json(document),
  );
  await user.upload(picker(), [pdf('first.pdf'), pdf('bad.pdf'), pdf('duplicate.pdf')]);
  await user.click(screen.getByRole('button', { name: 'Upload' }));
  await waitFor(() =>
    expect(screen.getByRole('status')).toHaveTextContent('3 of 3 files processed'),
  );
  const results = within(screen.getByRole('list', { name: 'Upload results' }));
  expect(results.getAllByRole('link', { name: 'Stored document doc-1' })).toHaveLength(2);
  expect(results.getByRole('alert')).toHaveTextContent('PDF could not be parsed');
  expect(screen.queryByText(/PRIVATE/)).not.toBeInTheDocument();
  expect(fetcher.mock.calls.filter(([request]) => request.method === 'POST')).toHaveLength(3);
  expect(screen.getAllByRole('link', { name: 'synthetic.pdf' })).toHaveLength(1);
});

test('pending upload displays progress and disables duplicate submission and selection', async () => {
  let finish!: (response: Response) => void;
  const pending = new Promise<Response>((resolve) => {
    finish = resolve;
  });
  const { user, picker, fetcher } = setup(() => pending);
  await user.upload(picker(), pdf());
  await user.click(screen.getByRole('button', { name: 'Upload' }));
  expect(screen.getByRole('status')).toHaveTextContent('Uploading · 0 of 1');
  expect(screen.getByRole('button', { name: 'Upload' })).toBeDisabled();
  expect(picker()).toBeDisabled();
  await user.click(screen.getByRole('button', { name: 'Upload' }));
  expect(fetcher.mock.calls.filter(([request]) => request.method === 'POST')).toHaveLength(1);
  await act(async () => finish(Response.json(document)));
  await waitFor(() =>
    expect(screen.getByRole('status')).toHaveTextContent('Upload batch complete'),
  );
});

test.each([
  [413, 'size limit'],
  [415, 'PDF files only'],
  [422, 'could not be parsed'],
  [503, 'may already be stored'],
  [500, 'HTTP 500'],
  [502, 'HTTP 502'],
  [504, 'outcome may be uncertain'],
])('HTTP %s is sanitized with no automatic POST retry', async (status, message) => {
  const { user, picker, fetcher } = setup(() =>
    Response.json({ detail: 'PRIVATE PDF TEXT' }, { status }),
  );
  await user.upload(picker(), pdf());
  await user.click(screen.getByRole('button', { name: 'Upload' }));
  expect(await screen.findByRole('alert')).toHaveTextContent(message);
  expect(screen.queryByText(/PRIVATE/)).not.toBeInTheDocument();
  expect(fetcher.mock.calls.filter(([request]) => request.method === 'POST')).toHaveLength(1);
});

test.each([
  [
    'network failure',
    () => {
      throw Error('PRIVATE');
    },
  ],
  ['HTML response', () => new Response('<html>PRIVATE</html>')],
  [
    'malformed JSON',
    () => new Response('{PRIVATE', { headers: { 'content-type': 'application/json' } }),
  ],
  ['invalid document', () => Response.json({ id: 'PRIVATE' })],
] as const)('%s reports uncertain outcome without response leakage', async (_label, response) => {
  const { user, picker, fetcher } = setup(response);
  await user.upload(picker(), pdf());
  await user.click(screen.getByRole('button', { name: 'Upload' }));
  expect(await screen.findByRole('alert')).toHaveTextContent(/outcome unknown/);
  expect(fetcher.mock.calls.filter(([request]) => request.method === 'POST')).toHaveLength(1);
  expect(screen.queryByText(/PRIVATE/)).not.toBeInTheDocument();
});

test('non-PDF and empty selections are rejected without POST requests', async () => {
  const { picker, fetcher } = setup();
  const user = userEvent.setup({ applyAccept: false });
  await user.upload(picker(), [
    new File(['text'], 'notes.txt', { type: 'text/plain' }),
    new File([], 'empty.pdf', { type: 'application/pdf' }),
    pdf('disguised.pdf', 'text/plain'),
  ]);
  await user.click(screen.getByRole('button', { name: 'Upload' }));
  await waitFor(() =>
    expect(screen.getByRole('status')).toHaveTextContent('3 of 3 files processed'),
  );
  expect(screen.getAllByRole('alert')).toHaveLength(3);
  expect(fetcher.mock.calls.filter(([request]) => request.method === 'POST')).toHaveLength(0);
});

test('later batches retain previous results and allow PDFs with unspecified MIME', async () => {
  const { user, picker } = setup();
  await user.upload(picker(), pdf('first.pdf'));
  await user.click(screen.getByRole('button', { name: 'Upload' }));
  await screen.findByRole('link', { name: 'Stored document doc-1' });
  await user.upload(picker(), pdf('SECOND.PDF', ''));
  await user.click(screen.getByRole('button', { name: 'Upload' }));
  await waitFor(() =>
    expect(screen.getAllByRole('link', { name: 'Stored document doc-1' })).toHaveLength(2),
  );
  expect(
    within(screen.getByRole('list', { name: 'Upload results' })).getByText('first.pdf'),
  ).toBeInTheDocument();
});

test('unconfigured integration has no upload picker and makes no requests', () => {
  const { fetcher } = setup(undefined, false);
  expect(screen.queryByLabelText('PDF files')).not.toBeInTheDocument();
  expect(fetcher).not.toHaveBeenCalled();
});

test('retained document is refreshed even when indexing returns 503', async () => {
  const { user, picker } = setup(
    () => Response.json({ detail: 'PRIVATE' }, { status: 503 }),
    true,
    true,
  );
  await user.upload(picker(), pdf());
  await user.click(screen.getByRole('button', { name: 'Upload' }));
  expect(await screen.findByRole('alert')).toHaveTextContent('may already be stored');
  expect(await screen.findByRole('link', { name: 'synthetic.pdf' })).toBeInTheDocument();
});

test('leaving the page stops files that have not yet been submitted', async () => {
  let finish!: (response: Response) => void;
  const pending = new Promise<Response>((resolve) => {
    finish = resolve;
  });
  const { user, picker, fetcher, unmount } = setup(() => pending);
  await user.upload(picker(), [pdf('first.pdf'), pdf('not-started.pdf')]);
  await user.click(screen.getByRole('button', { name: 'Upload' }));
  unmount();
  await act(async () => finish(Response.json(document)));
  expect(fetcher.mock.calls.filter(([request]) => request.method === 'POST')).toHaveLength(1);
});
