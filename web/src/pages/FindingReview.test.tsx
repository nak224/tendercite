import { act, fireEvent, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, expect, test, vi } from 'vitest';
import { App } from '../App';
import { QueryClient } from '@tanstack/react-query';
import type { ReviewEvent, ReviewRequest } from '../api/tenderciteReviews';
import { reviewEvent, reviewFinding, original } from '../test/reviews';

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  window.history.replaceState({}, '', '/');
});
function setup(
  options: {
    post?: (request: ReviewRequest) => Response | Promise<Response>;
    events?: ReviewEvent[];
    refreshFailure?: boolean;
  } = {},
) {
  let finding = structuredClone(reviewFinding);
  let events = options.events ?? [];
  if (events.length) {
    const latest = events.at(-1)!;
    finding.effective_value = latest.reviewed_value;
    finding.review_status = { CONFIRM: 'CONFIRMED', MODIFY: 'MODIFIED', REJECT: 'REJECTED' }[
      latest.action
    ];
  }
  const posts: ReviewRequest[] = [];
  const gets: string[] = [];
  function persist(request: ReviewRequest) {
    const event = reviewEvent(request, finding, `event-${events.length + 1}`);
    events = [...events, event];
    finding = {
      ...finding,
      effective_value: event.reviewed_value,
      review_status: { CONFIRM: 'CONFIRMED', MODIFY: 'MODIFIED', REJECT: 'REJECTED' }[event.action],
    };
    return event;
  }
  const fetcher = vi.fn(async (request: Request) => {
    const url = new URL(request.url);
    if (request.method === 'POST') {
      expect(url.pathname).toBe('/tendercite-api/api/v1/findings/finding-1/reviews');
      const payload: ReviewRequest = await request.json();
      posts.push(payload);
      return options.post
        ? options.post(payload)
        : Response.json(persist(payload), { status: 201 });
    }
    gets.push(url.pathname);
    if (posts.length && options.refreshFailure)
      return Response.json({ detail: 'PRIVATE' }, { status: 503 });
    if (url.pathname.endsWith('/reviews')) return Response.json(events);
    if (url.pathname.endsWith('/findings/finding-1')) return Response.json(finding);
    if (url.pathname.endsWith('/findings')) return Response.json([finding]);
    if (url.pathname.endsWith('/analyses/analysis-1'))
      return Response.json({
        id: 'analysis-1',
        provider: 'manual',
        model: 'none',
        created_at: '2026-10-21',
      });
    throw Error('Unexpected GET');
  });
  vi.stubGlobal('fetch', fetcher);
  vi.stubGlobal('__TENDERCITE_CONFIGURED__', true);
  window.history.replaceState({}, '', '/tenders/findings/finding-1');
  render(<App />);
  return { posts, gets, persist, user: userEvent.setup() };
}
async function form(action = 'CONFIRM') {
  const section = within(await screen.findByRole('region', { name: 'Human finding review' }));
  await screen.findByText('No review events. This finding has not been reviewed.');
  fireEvent.change(section.getByLabelText('Review action'), { target: { value: action } });
  return section;
}
test('UNREVIEWED source-backed finding remains visible with evidence and separate evidence boundaries', async () => {
  const { posts } = setup();
  await form();
  expect(screen.getByText(/Review: UNREVIEWED/)).toBeInTheDocument();
  expect(screen.getByText(`Original statement: ${original.statement}`)).toBeInTheDocument();
  expect(screen.getByText(/CONFIRMED does not mean FULFILLED/)).toBeInTheDocument();
  expect(screen.getByText(/no optimistic concurrency check/)).toBeInTheDocument();
  expect(screen.getByRole('link', { name: 'View source PDF page 2' })).toHaveAttribute(
    'href',
    '/tenders/documents/doc-1/pages/2',
  );
  expect(posts).toHaveLength(0);
});
test.each([
  ['CONFIRM', 'CONFIRMED'],
  ['REJECT', 'REJECTED'],
])('explicit %s posts once and reloads authoritative state and history', async (action, status) => {
  const { posts, gets, user } = setup();
  const section = await form(action);
  fireEvent.change(section.getByLabelText('Review comment (optional)'), {
    target: { value: 'Synthetic operator comment' },
  });
  expect(posts).toHaveLength(0);
  await user.click(section.getByRole('button', { name: 'Save Review' }));
  expect(await screen.findByText(new RegExp(`Review: ${status}`))).toBeInTheDocument();
  expect(posts).toEqual([{ action, comment: 'Synthetic operator comment' }]);
  expect(await screen.findByText(/Review saved:/)).toBeInTheDocument();
  expect(
    within(screen.getByRole('region', { name: 'Review history' })).getByText(
      'Comment: Synthetic operator comment',
    ),
  ).toBeInTheDocument();
  expect(gets.filter((path) => path.endsWith('/reviews'))).toHaveLength(2);
  expect(gets.filter((path) => path.endsWith('/findings/finding-1'))).toHaveLength(2);
});
test('MODIFY posts complete exact value, retains original and updates effective finding and analysis list', async () => {
  const { posts, user } = setup();
  const invalidate = vi.spyOn(QueryClient.prototype, 'invalidateQueries');
  const section = await form('MODIFY');
  fireEvent.change(section.getByLabelText('Reviewed requirement statement'), {
    target: { value: '  Revised synthetic statement.\n' },
  });
  fireEvent.change(section.getByLabelText('Reviewed category'), { target: { value: 'RISK' } });
  fireEvent.change(section.getByLabelText('Reviewed requirement type'), {
    target: { value: 'SECURITY' },
  });
  await user.click(section.getByRole('button', { name: 'Save Review' }));
  expect(await screen.findByText(/Review: MODIFIED/)).toBeInTheDocument();
  expect(posts).toEqual([
    {
      action: 'MODIFY',
      reviewed_value: {
        statement: '  Revised synthetic statement.\n',
        category: 'RISK',
        requirement_type: 'SECURITY',
      },
      comment: '',
    },
  ]);
  expect(screen.getByRole('heading', { name: 'Revised synthetic statement.' })).toBeInTheDocument();
  expect(screen.getByText(`Original statement: ${original.statement}`)).toBeInTheDocument();
  const predicate = invalidate.mock.calls[0]?.[0]?.predicate;
  expect(predicate).toBeTypeOf('function');
  const cache = new QueryClient();
  const matches = (queryKey: readonly unknown[]) =>
    predicate?.(cache.getQueryCache().build(cache, { queryKey }));
  for (const path of ['findings', 'findings?analysis_run_id=analysis-1', 'analyses/analysis-1'])
    expect(matches(['tendercite', path])).toBe(true);
  expect(matches(['workforce'])).toBe(false);
  expect(matches(['tendercite', 'documents'])).toBe(false);
  await user.click(screen.getByRole('link', { name: 'Analysis analysis-1' }));
  expect(
    await screen.findByRole('link', { name: 'Revised synthetic statement.' }),
  ).toBeInTheDocument();
});
test('ordered prior history is rendered and MODIFY starts from effective interpretation', async () => {
  const first = reviewEvent({
    action: 'MODIFY',
    reviewed_value: { ...original, statement: 'Earlier reviewed interpretation' },
    comment: 'First synthetic review',
  });
  setup({ events: [first] });
  const section = within(await screen.findByRole('region', { name: 'Human finding review' }));
  expect(await screen.findByText('Comment: First synthetic review')).toBeInTheDocument();
  fireEvent.change(section.getByLabelText('Review action'), { target: { value: 'MODIFY' } });
  expect(section.getByLabelText('Reviewed requirement statement')).toHaveValue(
    'Earlier reviewed interpretation',
  );
  expect(screen.getByText(`Original statement: ${original.statement}`)).toBeInTheDocument();
});
test('overlapping saves and reloads are blocked until the request and authoritative refresh finish', async () => {
  let finish!: (response: Response) => void;
  const pending = new Promise<Response>((resolve) => {
    finish = resolve;
  });
  const { posts, user } = setup({ post: () => pending });
  const section = await form();
  await user.click(section.getByRole('button', { name: 'Save Review' }));
  expect(section.getByLabelText('Review action')).toBeDisabled();
  expect(section.getByRole('button', { name: 'Reload finding and history' })).toBeDisabled();
  fireEvent.submit(section.getByRole('button', { name: 'Save Review' }).closest('form')!);
  expect(posts).toHaveLength(1);
  await act(async () => finish(Response.json(reviewEvent(posts[0]!))));
  expect(await screen.findByText(/Review saved:/)).toBeInTheDocument();
});
test.each(['HTTP', 'network', 'HTML', 'wrong-event'])(
  'uncertain %s outcome never retries, preserves edited form and reloads current finding/history',
  async (kind) => {
    const { posts, gets, user } = setup({
      post: (request) => {
        if (kind === 'HTTP') return Response.json({ detail: 'PRIVATE' }, { status: 422 });
        if (kind === 'network') throw Error('PRIVATE');
        if (kind === 'HTML') return new Response('<html>PRIVATE</html>');
        return Response.json({ ...reviewEvent(request), finding_id: 'other' });
      },
    });
    const section = await form('MODIFY');
    fireEvent.change(section.getByLabelText('Reviewed requirement statement'), {
      target: { value: 'Unsaved operator interpretation' },
    });
    fireEvent.change(section.getByLabelText('Review comment (optional)'), {
      target: { value: 'Retain this comment' },
    });
    await user.click(section.getByRole('button', { name: 'Save Review' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('does not prove nothing was saved');
    if (kind === 'HTTP') expect(screen.getByRole('alert')).toHaveTextContent('HTTP 422');
    expect(screen.queryByText(/PRIVATE/)).not.toBeInTheDocument();
    expect(screen.queryByText(/Review saved:/)).not.toBeInTheDocument();
    expect(section.getByLabelText('Reviewed requirement statement')).toHaveValue(
      'Unsaved operator interpretation',
    );
    expect(section.getByLabelText('Review comment (optional)')).toHaveValue('Retain this comment');
    expect(screen.getByRole('heading', { name: original.statement })).toBeInTheDocument();
    expect(posts).toHaveLength(1);
    expect(gets.filter((path) => path.endsWith('/reviews'))).toHaveLength(2);
    expect(gets.filter((path) => path.endsWith('/findings/finding-1'))).toHaveLength(2);
  },
);
test('lost response can still reveal a persisted review without claiming confirmed POST success', async () => {
  const configured: Parameters<typeof setup>[0] = {};
  const state = setup(configured);
  configured.post = (request) => {
    state.persist(request);
    throw Error('Lost response');
  };
  const section = await form();
  await state.user.click(section.getByRole('button', { name: 'Save Review' }));
  expect(await screen.findByText(/Review: CONFIRMED/)).toBeInTheDocument();
  expect(screen.getByRole('alert')).toHaveTextContent('uncertain');
  expect(screen.queryByText(/Review saved:/)).not.toBeInTheDocument();
  expect(
    within(screen.getByRole('region', { name: 'Review history' })).getAllByRole('listitem'),
  ).toHaveLength(1);
  expect(state.posts).toHaveLength(1);
});
test('refresh failure keeps selected finding and form visible and blocks another save', async () => {
  const { user, posts } = setup({ refreshFailure: true });
  const section = await form();
  await user.click(section.getByRole('button', { name: 'Save Review' }));
  expect(
    await screen.findByText(/Could not reload current finding and history/),
  ).toBeInTheDocument();
  expect(screen.getByRole('heading', { name: original.statement })).toBeInTheDocument();
  expect(section.getByRole('button', { name: 'Save Review' })).toBeDisabled();
  expect(section.getByLabelText('Review action')).toHaveValue('CONFIRM');
  expect(posts).toHaveLength(1);
});

test('reload uses current effective interpretation for untouched MODIFY input and preserves actual edits', async () => {
  const state = setup();
  const section = await form();
  state.persist({
    action: 'MODIFY',
    reviewed_value: { ...original, statement: 'Another operator changed this' },
    comment: '',
  });
  await state.user.click(section.getByRole('button', { name: 'Reload finding and history' }));
  expect(
    await screen.findByRole('heading', { name: 'Another operator changed this' }),
  ).toBeInTheDocument();
  fireEvent.change(section.getByLabelText('Review action'), { target: { value: 'MODIFY' } });
  expect(section.getByLabelText('Reviewed requirement statement')).toHaveValue(
    'Another operator changed this',
  );
  fireEvent.change(section.getByLabelText('Reviewed requirement statement'), {
    target: { value: 'Preserve operator draft' },
  });
  state.persist({
    action: 'MODIFY',
    reviewed_value: { ...original, statement: 'Newer server interpretation' },
    comment: '',
  });
  await state.user.click(section.getByRole('button', { name: 'Reload finding and history' }));
  expect(
    await screen.findByRole('heading', { name: 'Newer server interpretation' }),
  ).toBeInTheDocument();
  expect(section.getByLabelText('Reviewed requirement statement')).toHaveValue(
    'Preserve operator draft',
  );
  expect(state.posts).toHaveLength(0);
});

test('failed reload can recover explicitly without submitting the review twice', async () => {
  const options = { refreshFailure: true };
  const state = setup(options);
  const section = await form();
  await state.user.click(section.getByRole('button', { name: 'Save Review' }));
  expect(
    await screen.findByText(/Could not reload current finding and history/),
  ).toBeInTheDocument();
  expect(section.getByRole('button', { name: 'Save Review' })).toBeDisabled();
  options.refreshFailure = false;
  await state.user.click(section.getByRole('button', { name: 'Reload finding and history' }));
  expect(await screen.findByText(/Review: CONFIRMED/)).toBeInTheDocument();
  expect(
    screen.queryByText(/Could not reload current finding and history/),
  ).not.toBeInTheDocument();
  expect(section.getByRole('button', { name: 'Save Review' })).toBeEnabled();
  expect(state.posts).toHaveLength(1);
});
