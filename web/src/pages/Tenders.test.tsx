import { afterEach, expect, test, vi } from 'vitest';
import { act, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { App } from '../App';
import { render } from '@testing-library/react';

const document = {
  id: 'doc-1',
  filename: 'Synthetic tender.pdf',
  sha256: 'a'.repeat(64),
  page_count: 2,
  chunk_count: 3,
  created_at: '2026-10-15',
};
const finding = {
  id: 'finding-1',
  analysis_run_id: 'analysis-1',
  statement: 'Original requirement',
  category: 'MUST',
  requirement_type: 'TECHNICAL',
  effective_value: {
    statement: 'Reviewed synthetic requirement',
    category: 'MUST',
    requirement_type: 'TECHNICAL',
  },
  review_status: 'MODIFIED',
  grounding_status: 'VERIFIED_QUOTE',
  evidence: [
    {
      document_id: 'doc-1',
      page_number: 2,
      quote: 'Synthetic supporting quote',
      grounding_status: 'VERIFIED_QUOTE',
    },
  ],
};
afterEach(() => {
  vi.unstubAllGlobals();
  window.history.replaceState({}, '', '/');
});
function setup(path = '/tenders', response?: (path: string) => Response | Promise<Response>) {
  window.history.replaceState({}, '', path);
  const fetcher = vi.fn((request: Request) => {
    expect(request.method).toBe('GET');
    const path = new URL(request.url).pathname;
    if (response) return Promise.resolve(response(path));
    if (path.endsWith('/documents')) return Promise.resolve(Response.json([document]));
    if (path.endsWith('/documents/doc-1')) return Promise.resolve(Response.json(document));
    if (path.endsWith('/reviews')) return Promise.resolve(Response.json([]));
    if (path.endsWith('/findings/finding-1')) return Promise.resolve(Response.json(finding));
    if (path.endsWith('/findings')) return Promise.resolve(Response.json([finding]));
    if (path.endsWith('/analyses/analysis-1'))
      return Promise.resolve(
        Response.json({
          id: 'analysis-1',
          provider: 'manual',
          model: 'none',
          created_at: '2026-10-15',
        }),
      );
    throw new Error('Unexpected endpoint');
  });
  vi.stubGlobal('fetch', fetcher);
  render(<App />);
  return fetcher;
}
test('navigation opens an unconfigured tender page without requests or period controls', async () => {
  vi.stubGlobal('__TENDERCITE_CONFIGURED__', false);
  const fetcher = setup('/missing');
  await userEvent.click(screen.getByRole('link', { name: 'Documents & findings' }));
  expect(screen.getByText(/TenderCite is not configured/)).toBeInTheDocument();
  expect(screen.queryByRole('region', { name: 'Planning period' })).not.toBeInTheDocument();
  expect(fetcher).not.toHaveBeenCalled();
});
test('configured documents, current findings, analysis and evidence details are navigable', async () => {
  vi.stubGlobal('__TENDERCITE_CONFIGURED__', true);
  setup();
  await userEvent.click(await screen.findByRole('link', { name: document.filename }));
  expect(await screen.findByText(`SHA-256: ${document.sha256}`)).toBeInTheDocument();
  await userEvent.click(screen.getByRole('link', { name: 'All tenders' }));
  await userEvent.click(
    await screen.findByRole('link', { name: finding.effective_value.statement }),
  );
  expect(await screen.findByText('Synthetic supporting quote')).toBeInTheDocument();
  expect(screen.getByText(/Review: MODIFIED/)).toBeInTheDocument();
  await userEvent.click(screen.getByRole('link', { name: 'Analysis analysis-1' }));
  expect(await screen.findByRole('heading', { name: 'Analysis analysis-1' })).toBeInTheDocument();
  expect(
    await screen.findByRole('link', { name: finding.effective_value.statement }),
  ).toBeInTheDocument();
});
test.each([404, 500, 502])(
  'HTTP %s uses safe messages rather than private error bodies',
  async (status) => {
    vi.stubGlobal('__TENDERCITE_CONFIGURED__', true);
    setup('/tenders/documents/doc-1', () => Response.json({ detail: 'PRIVATE' }, { status }));
    expect(await screen.findByRole('alert')).toHaveTextContent(
      status === 404 ? 'record not found' : `HTTP ${status}`,
    );
    expect(screen.queryByText(/PRIVATE/)).not.toBeInTheDocument();
  },
);
test('network failure identifies backend unavailability', async () => {
  vi.stubGlobal('__TENDERCITE_CONFIGURED__', true);
  setup('/tenders/documents/doc-1', () => {
    throw new Error('PRIVATE');
  });
  expect(await screen.findByRole('alert')).toHaveTextContent('TenderCite unavailable');
});
test('HTML fallback is reported as invalid proxy response', async () => {
  vi.stubGlobal('__TENDERCITE_CONFIGURED__', true);
  setup('/tenders/documents/doc-1', () => new Response('<html/>'));
  expect(await screen.findByRole('alert')).toHaveTextContent('invalid response');
});
test.each(['/workforce', '/projects', '/planning', '/gaps'])(
  'Team Capacity route %s is absent and does not request workforce data',
  (path) => {
    const fetcher = setup(path);
    expect(screen.queryByRole('region', { name: 'Planning period' })).not.toBeInTheDocument();
    expect(screen.getByRole('alert')).toHaveTextContent('Page not found');
    expect(fetcher).not.toHaveBeenCalled();
  },
);

const sourceText =
  '  Synthetic page body\n\n  Original whitespace.\t<script>inert text</script>  \n';
const sourcePage = { document_id: 'doc-1', page_number: 2, text: sourceText };

test('citation opens the exact stored physical page on demand without changing layout', async () => {
  vi.stubGlobal('__TENDERCITE_CONFIGURED__', true);
  const fetcher = setup('/tenders/findings/finding-1', (path) =>
    Response.json(path.endsWith('/pages/2') ? sourcePage : finding),
  );
  const link = await screen.findByRole('link', { name: 'View source PDF page 2' });
  expect(link).toHaveAttribute('href', '/tenders/documents/doc-1/pages/2');
  expect(fetcher.mock.calls.some(([request]) => request.url.includes('/pages/'))).toBe(false);
  await userEvent.click(link);
  expect(await screen.findByRole('heading', { name: 'Source PDF page 2' })).toBeInTheDocument();
  const text = await screen.findByText(/Synthetic page body/);
  expect(text.tagName).toBe('PRE');
  expect(text.textContent).toBe(sourceText);
  expect(text.querySelector('script')).toBeNull();
  expect(screen.getByText(/not interpretation correctness/)).toBeInTheDocument();
  expect(screen.getByRole('link', { name: 'Document doc-1' })).toHaveAttribute(
    'href',
    '/tenders/documents/doc-1',
  );
  expect(screen.queryByRole('region', { name: 'Planning period' })).not.toBeInTheDocument();
  expect(screen.getByRole('navigation', { name: 'Primary navigation' })).toBeInTheDocument();
  expect(
    new URL(fetcher.mock.calls.find(([request]) => request.url.includes('/pages/'))![0].url)
      .pathname,
  ).toBe('/tendercite-api/api/v1/documents/doc-1/pages/2');
});

test('each citation retains its own document and physical page identity', async () => {
  vi.stubGlobal('__TENDERCITE_CONFIGURED__', true);
  setup('/tenders/findings/finding-1', () =>
    Response.json({
      ...finding,
      evidence: [
        ...finding.evidence,
        { ...finding.evidence[0], document_id: 'doc-2', page_number: 7 },
      ],
    }),
  );
  expect(await screen.findByRole('link', { name: 'View source PDF page 2' })).toHaveAttribute(
    'href',
    '/tenders/documents/doc-1/pages/2',
  );
  expect(screen.getByRole('link', { name: 'View source PDF page 7' })).toHaveAttribute(
    'href',
    '/tenders/documents/doc-2/pages/7',
  );
});

test('source page exposes loading state until the read completes', async () => {
  vi.stubGlobal('__TENDERCITE_CONFIGURED__', true);
  let finish!: (response: Response) => void;
  const pending = new Promise<Response>((resolve) => {
    finish = resolve;
  });
  setup('/tenders/documents/doc-1/pages/2', () => pending);
  expect(screen.getByRole('status')).toHaveTextContent('Loading');
  await act(async () => finish(Response.json(sourcePage)));
  expect(await screen.findByText(/Synthetic page body/)).toBeInTheDocument();
});

test.each([
  ['missing page', () => Response.json({ detail: 'PRIVATE' }, { status: 404 }), 'record not found'],
  ['backend error', () => Response.json({ detail: 'PRIVATE' }, { status: 503 }), 'HTTP 503'],
  [
    'network failure',
    () => {
      throw new Error('PRIVATE');
    },
    'TenderCite unavailable',
  ],
  ['HTML fallback', () => new Response('<html>PRIVATE</html>'), 'invalid response'],
  [
    'malformed JSON',
    () => new Response('{PRIVATE', { headers: { 'Content-Type': 'application/json' } }),
    'invalid response',
  ],
  [
    'wrong document',
    () => Response.json({ ...sourcePage, document_id: 'other-doc' }),
    'invalid source page',
  ],
  ['wrong page', () => Response.json({ ...sourcePage, page_number: 1 }), 'invalid source page'],
  [
    'string page number',
    () => Response.json({ ...sourcePage, page_number: '2' }),
    'invalid source page',
  ],
  [
    'missing text',
    () => Response.json({ document_id: 'doc-1', page_number: 2 }),
    'invalid source page',
  ],
  ['non-string text', () => Response.json({ ...sourcePage, text: null }), 'invalid source page'],
  ['null response', () => Response.json(null), 'invalid source page'],
  ['array response', () => Response.json([]), 'invalid source page'],
] as const)(
  'source page handles %s without substituting or leaking content',
  async (_label, response, message) => {
    vi.stubGlobal('__TENDERCITE_CONFIGURED__', true);
    setup('/tenders/documents/doc-1/pages/2', response);
    expect(await screen.findByRole('alert')).toHaveTextContent(message);
    expect(screen.queryByText(/PRIVATE|Synthetic page body/)).not.toBeInTheDocument();
    expect(screen.queryByText(finding.evidence[0]!.quote)).not.toBeInTheDocument();
  },
);

test('failed source page can be explicitly retried', async () => {
  vi.stubGlobal('__TENDERCITE_CONFIGURED__', true);
  let attempts = 0;
  setup('/tenders/documents/doc-1/pages/2', () =>
    ++attempts === 1 ? new Response('', { status: 503 }) : Response.json(sourcePage),
  );
  await screen.findByRole('alert');
  expect(attempts).toBe(1);
  await userEvent.click(screen.getByRole('button', { name: 'Retry' }));
  expect(await screen.findByText(/Synthetic page body/)).toBeInTheDocument();
  expect(attempts).toBe(2);
});

test('empty stored text is reported without invented source content', async () => {
  vi.stubGlobal('__TENDERCITE_CONFIGURED__', true);
  setup('/tenders/documents/doc-1/pages/2', () => Response.json({ ...sourcePage, text: '' }));
  expect(await screen.findByText('No extracted text is stored for this page.')).toBeInTheDocument();
});

test.each(['0', '-1', '1.5', 'not-a-page', '9007199254740992'])(
  'invalid page route %s performs no request',
  async (page) => {
    vi.stubGlobal('__TENDERCITE_CONFIGURED__', true);
    const fetcher = setup(`/tenders/documents/doc-1/pages/${page}`);
    expect(await screen.findByRole('alert')).toHaveTextContent('Invalid physical PDF page number');
    expect(fetcher).not.toHaveBeenCalled();
  },
);
