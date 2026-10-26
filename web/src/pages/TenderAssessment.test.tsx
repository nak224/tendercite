import { act, fireEvent, render, screen, within, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, expect, test, vi } from 'vitest';
import { App } from '../App';
import { assessmentStatuses, type AssessmentRequest } from '../api/tenderciteAssessments';
import { assessmentFindings, matrixRow } from '../test/assessments';

afterEach(() => {
  vi.unstubAllGlobals();
  window.history.replaceState({}, '', '/');
});
function setup(
  options: {
    patch?: (request: AssessmentRequest) => Response | Promise<Response>;
    refreshFailure?: boolean;
    configured?: boolean;
    empty?: boolean;
    secondEligible?: boolean;
    unverifiedGrounding?: boolean;
    path?: string;
  } = {},
) {
  const findings = options.empty ? [] : structuredClone(assessmentFindings);
  if (options.secondEligible) findings[1]!.review_status = 'MODIFIED';
  if (options.unverifiedGrounding) {
    findings[0]!.grounding_status = 'INVALID_QUOTE';
    findings[0]!.evidence[0]!.grounding_status = 'INVALID_QUOTE';
  }
  let rows = findings
    .filter((f) => ['CONFIRMED', 'MODIFIED'].includes(f.review_status))
    .map((f) => matrixRow(f));
  const patches: AssessmentRequest[] = [];
  const gets: string[] = [];
  function persist(request: AssessmentRequest) {
    const row = matrixRow(findings[0], request);
    rows = rows.map((stored) => (stored.finding_id === row.finding_id ? row : stored));
    return row;
  }
  function reviewAgain(status = 'CONFIRMED') {
    findings[0]!.review_status = status;
    rows = ['CONFIRMED', 'MODIFIED'].includes(status) ? [matrixRow(findings[0])] : [];
  }
  const fetcher = vi.fn(async (request: Request) => {
    const url = new URL(request.url);
    if (request.method === 'PATCH') {
      expect(url.pathname).toBe('/tendercite-api/api/v1/go-no-go/finding-1');
      const payload: AssessmentRequest = await request.json();
      patches.push(payload);
      return options.patch ? options.patch(payload) : Response.json(persist(payload));
    }
    expect(request.method).toBe('GET');
    gets.push(url.pathname);
    if (patches.length && options.refreshFailure)
      return Response.json({ detail: 'PRIVATE' }, { status: 503 });
    if (url.pathname.endsWith('/findings')) {
      expect(url.searchParams.get('analysis_run_id')).toBe('analysis-1');
      return Response.json(findings);
    }
    if (url.pathname.endsWith('/go-no-go')) {
      expect(url.searchParams.get('analysis_run_id')).toBe('analysis-1');
      return Response.json(rows);
    }
    if (url.pathname.endsWith('/analyses/analysis-1'))
      return Response.json({
        id: 'analysis-1',
        provider: 'manual',
        model: 'none',
        created_at: '2026-10-21',
      });
    throw Error('Unexpected endpoint');
  });
  vi.stubGlobal('fetch', fetcher);
  vi.stubGlobal('__TENDERCITE_CONFIGURED__', options.configured !== false);
  window.history.replaceState({}, '', options.path ?? '/tenders/analyses/analysis-1/assessment');
  render(<App />);
  return { patches, gets, persist, reviewAgain, fetcher, user: userEvent.setup() };
}
async function editor() {
  return within(await screen.findByRole('article', { name: 'Assessment for finding-1' }));
}
function fill(
  section: ReturnType<typeof within>,
  status = 'FULFILLED',
  rationale = 'Synthetic operator judgment',
) {
  fireEvent.change(section.getByLabelText('Assessment status'), { target: { value: status } });
  fireEvent.change(section.getByLabelText('Assessment rationale'), {
    target: { value: rationale },
  });
}
test('analysis page links to scoped matrix with clear eligible, unreviewed and rejected groups', async () => {
  const state = setup({ path: '/tenders/analyses/analysis-1' });
  await state.user.click(await screen.findByRole('link', { name: 'Go/No-Go Assessment' }));
  const section = await editor();
  expect(section.getByText('Current assessment: CLARIFICATION_NEEDED')).toBeInTheDocument();
  for (const name of ['Findings requiring human review', 'Rejected findings excluded']) {
    const group = within(screen.getByRole('region', { name }));
    expect(group.queryByRole('button', { name: 'Save Assessment' })).not.toBeInTheDocument();
    expect(
      group.getByRole('link', { name: 'Finding details and source evidence' }),
    ).toBeInTheDocument();
  }
  expect(section.getByRole('link', { name: /physical PDF page 2/ })).toHaveAttribute(
    'href',
    '/tenders/documents/doc-1/pages/2',
  );
  expect(
    section.getByRole('link', { name: 'Finding details and source evidence' }),
  ).toHaveAttribute('href', '/tenders/findings/finding-1');
  expect(screen.getByText(/no automatic overall Go\/No-Go verdict/)).toBeInTheDocument();
  expect(screen.queryByRole('region', { name: 'Planning period' })).not.toBeInTheDocument();
  expect(state.patches).toHaveLength(0);
});
test.each(assessmentStatuses)(
  'explicit %s sends exact PATCH and reloads authoritative status/rationale',
  async (status) => {
    const { patches, gets, user } = setup();
    const section = await editor();
    fill(section, status, '  Explicit synthetic judgment.\n');
    expect(patches).toHaveLength(0);
    await user.click(section.getByRole('button', { name: 'Save Assessment' }));
    expect(await screen.findByText(`Current assessment: ${status}`)).toBeInTheDocument();
    expect(await screen.findByText(/Assessment saved for finding-1/)).toBeInTheDocument();
    expect(patches).toEqual([{ status, rationale: '  Explicit synthetic judgment.\n' }]);
    await waitFor(() => expect(gets.filter((path) => path.endsWith('/go-no-go'))).toHaveLength(2));
    expect(gets.filter((path) => path.endsWith('/findings'))).toHaveLength(2);
    expect((await editor()).getByLabelText('Assessment status')).toHaveValue('');
  },
);
test.each(['', '   ', 'x'.repeat(4001)])(
  'blank or oversized rationale never sends a mutation (%#)',
  async (rationale) => {
    const { patches } = setup();
    const section = await editor();
    fill(section, 'MISSING', rationale);
    fireEvent.submit(section.getByRole('button', { name: 'Save Assessment' }).closest('form')!);
    expect(await screen.findByRole('alert')).toHaveTextContent('nonblank rationale');
    expect(patches).toHaveLength(0);
  },
);
test('overlapping mutations are blocked globally until refresh finishes', async () => {
  let finish!: (response: Response) => void;
  const pending = new Promise<Response>((resolve) => {
    finish = resolve;
  });
  const state = setup({ patch: () => pending, secondEligible: true });
  const section = await editor();
  fill(section);
  await state.user.click(section.getByRole('button', { name: 'Save Assessment' }));
  expect(section.getByLabelText('Assessment rationale')).toBeDisabled();
  expect(screen.getByRole('button', { name: 'Reload assessment matrix' })).toBeDisabled();
  fireEvent.submit(section.getByRole('button', { name: 'Save Assessment' }).closest('form')!);
  const second = within(screen.getByRole('article', { name: 'Assessment for finding-2' }));
  expect(second.getByRole('button', { name: 'Save Assessment' })).toBeDisabled();
  fireEvent.submit(second.getByRole('button', { name: 'Save Assessment' }).closest('form')!);
  expect(state.patches).toHaveLength(1);
  await act(async () => finish(Response.json(state.persist(state.patches[0]!))));
  expect(await screen.findByText(/Assessment saved for finding-1/)).toBeInTheDocument();
});
test.each(['HTTP', 'network', 'invalid-response', 'mismatched-response'])(
  '%s errors preserve uncertainty, sanitize bodies, reload and never retry',
  async (kind) => {
    const state = setup({
      patch: (request) => {
        if (kind === 'HTTP') return Response.json({ detail: 'PRIVATE' }, { status: 500 });
        if (kind === 'network') throw Error('PRIVATE');
        if (kind === 'invalid-response') return new Response('<html>PRIVATE</html>');
        return Response.json({ ...matrixRow(undefined, request), finding_id: 'different' });
      },
    });
    const section = await editor();
    fill(section);
    await state.user.click(section.getByRole('button', { name: 'Save Assessment' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('does not prove nothing was saved');
    expect(screen.queryByText(/PRIVATE/)).not.toBeInTheDocument();
    expect(screen.queryByText(/Assessment saved for/)).not.toBeInTheDocument();
    await waitFor(() =>
      expect(state.gets.filter((path) => path.endsWith('/go-no-go'))).toHaveLength(2),
    );
    expect(state.patches).toHaveLength(1);
    expect(screen.getByText('Current assessment: CLARIFICATION_NEEDED')).toBeInTheDocument();
  },
);
test('HTTP 409 reloads newly rejected finding into excluded group without restoring old assessment', async () => {
  const options: Parameters<typeof setup>[0] = {};
  const state = setup(options);
  options.patch = () => {
    state.reviewAgain('REJECTED');
    return Response.json({ detail: 'PRIVATE' }, { status: 409 });
  };
  const section = await editor();
  fill(section);
  await state.user.click(section.getByRole('button', { name: 'Save Assessment' }));
  expect(await screen.findByRole('alert')).toHaveTextContent('HTTP 409');
  await waitFor(() =>
    expect(
      screen.queryByRole('article', { name: 'Assessment for finding-1' }),
    ).not.toBeInTheDocument(),
  );
  expect(screen.getByRole('region', { name: 'Rejected findings excluded' })).toHaveTextContent(
    'REJECTED — excluded from assessment (2)',
  );
  expect(state.patches).toHaveLength(1);
});
test('a lost response may still persist; readback shows actual status without claiming confirmed success', async () => {
  const options: Parameters<typeof setup>[0] = {};
  const state = setup(options);
  options.patch = (request) => {
    state.persist(request);
    throw Error('Lost response');
  };
  const section = await editor();
  fill(section, 'PARTIAL');
  await state.user.click(section.getByRole('button', { name: 'Save Assessment' }));
  expect(await screen.findByText('Current assessment: PARTIAL')).toBeInTheDocument();
  expect(screen.getByRole('alert')).toHaveTextContent('uncertain');
  expect(screen.queryByText(/Assessment saved for/)).not.toBeInTheDocument();
  expect(state.patches).toHaveLength(1);
});
test('new identical review resets prior assessment and clears pending frontend drafts on reload', async () => {
  const state = setup();
  const section = await editor();
  fill(section);
  await state.user.click(section.getByRole('button', { name: 'Save Assessment' }));
  expect(await screen.findByText('Current assessment: FULFILLED')).toBeInTheDocument();
  fill(await editor(), 'FULFILLED', 'Do not carry this judgment into a new review');
  state.reviewAgain();
  await state.user.click(screen.getByRole('button', { name: 'Reload assessment matrix' }));
  expect(await screen.findByText('Current assessment: CLARIFICATION_NEEDED')).toBeInTheDocument();
  expect((await editor()).getByLabelText('Assessment status')).toHaveValue('');
  expect((await editor()).getByLabelText('Assessment rationale')).toHaveValue('');
  expect(
    screen.queryByText('Current rationale: Synthetic operator judgment'),
  ).not.toBeInTheDocument();
  expect(state.patches).toHaveLength(1);
  expect(screen.queryByText(/Assessment saved for/)).not.toBeInTheDocument();
});
test('failed readback retains selected analysis and blocks saves until explicit recovery', async () => {
  const options = { refreshFailure: true };
  const state = setup(options);
  const section = await editor();
  fill(section);
  await state.user.click(section.getByRole('button', { name: 'Save Assessment' }));
  expect(await screen.findByText(/Displayed data may be stale/)).toBeInTheDocument();
  expect(screen.getByRole('link', { name: 'Analysis analysis-1' })).toBeInTheDocument();
  expect(section.getByRole('button', { name: 'Save Assessment' })).toBeDisabled();
  options.refreshFailure = false;
  await state.user.click(screen.getByRole('button', { name: 'Reload assessment matrix' }));
  expect(await screen.findByText('Current assessment: FULFILLED')).toBeInTheDocument();
  expect(screen.queryByText(/Displayed data may be stale/)).not.toBeInTheDocument();
  expect(state.patches).toHaveLength(1);
});
test('empty analysis and unconfigured backend do not offer assessment writes', async () => {
  setup({ empty: true });
  expect(
    await screen.findByText('No reviewed findings eligible for manual assessment.'),
  ).toBeInTheDocument();
  expect(screen.queryByRole('button', { name: 'Save Assessment' })).not.toBeInTheDocument();
});
test('unconfigured backend never requests data', () => {
  const state = setup({ configured: false });
  expect(screen.getByText(/TenderCite is not configured/)).toBeInTheDocument();
  expect(state.fetcher).not.toHaveBeenCalled();
});

test('manual eligibility follows accepted review rather than inferring qualification from grounding', async () => {
  const state = setup({ secondEligible: true, unverifiedGrounding: true });
  const first = await editor();
  expect(first.getByText(/Source grounding is not verified/)).toBeInTheDocument();
  expect(first.getByRole('button', { name: 'Save Assessment' })).toBeEnabled();
  const second = within(screen.getByRole('article', { name: 'Assessment for finding-2' }));
  expect(second.getByText(/Review: MODIFIED/)).toBeInTheDocument();
  expect(second.getByRole('button', { name: 'Save Assessment' })).toBeEnabled();
  expect(state.patches).toHaveLength(0);
});
