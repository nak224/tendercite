import { act, fireEvent, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, expect, test, vi } from 'vitest';
import { App } from '../App';
import type { CuratedTenderIntake } from '../api/tenderciteIntake';
import { sources, quote, summary } from '../test/intake';

type Post = { operation: string; intake: CuratedTenderIntake; body: unknown };
afterEach(() => {
  vi.unstubAllGlobals();
  window.history.replaceState({}, '', '/');
});
function setup(
  reply?: (post: Post) => Response | Promise<Response>,
  path = '/tenders/intake',
  configured = true,
  catalogue?: () => Response,
) {
  const posts: Post[] = [];
  const fetcher = vi.fn(async (request: Request) => {
    const url = new URL(request.url);
    if (request.method === 'GET') {
      if (url.pathname.endsWith('/documents'))
        return catalogue
          ? catalogue()
          : Response.json(
              sources.map((doc) => ({ ...doc, chunk_count: 2, created_at: '2026-10-21' })),
            );
      if (url.pathname.endsWith('/findings')) return Response.json([]);
      throw Error('Unexpected GET');
    }
    const operation = url.pathname.split('/').at(-1)!;
    const body = JSON.parse(await request.text());
    const intake: CuratedTenderIntake = operation === 'commit' ? body.intake : body;
    const record = { operation, intake, body };
    posts.push(record);
    return reply
      ? reply(record)
      : Response.json(summary(intake, operation === 'commit' ? 'CREATED' : 'VALIDATED'));
  });
  vi.stubGlobal('fetch', fetcher);
  vi.stubGlobal('__TENDERCITE_CONFIGURED__', configured);
  window.history.replaceState({}, '', path);
  render(<App />);
  return { posts, fetcher, user: userEvent.setup() };
}
async function fill(number = 1, doc = 'doc-one') {
  const group = within(await screen.findByRole('group', { name: `Requirement ${number}` }));
  fireEvent.change(screen.getByLabelText('Intake purpose'), {
    target: { value: 'Synthetic operator purpose' },
  });
  fireEvent.change(group.getByLabelText('Requirement statement'), {
    target: { value: 'Synthetic operator requirement' },
  });
  fireEvent.change(group.getByLabelText('Category'), { target: { value: 'MUST' } });
  fireEvent.change(group.getByLabelText('Requirement type'), { target: { value: 'TECHNICAL' } });
  fireEvent.change(group.getByLabelText('Source document'), { target: { value: doc } });
  fireEvent.change(group.getByLabelText('Physical PDF page'), { target: { value: '2' } });
  fireEvent.change(group.getByLabelText('Exact supporting quotation'), {
    target: { value: quote },
  });
  return group;
}
async function validate(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByRole('button', { name: 'Validate' }));
  return screen.findByRole('region', { name: 'Validated intake' });
}
async function confirmAndSave(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByRole('checkbox'));
  await user.click(screen.getByRole('button', { name: 'Save requirements' }));
}

test('home navigation opens manual intake; no identity entry or generated quote', async () => {
  const { user, posts } = setup(undefined, '/tenders');
  await user.click(await screen.findByRole('link', { name: 'Create manual requirements' }));
  const group = within(await screen.findByRole('group', { name: 'Requirement 1' }));
  expect(group.getByLabelText('Exact supporting quotation')).toHaveValue('');
  expect(screen.queryByLabelText(/SHA|document ID|item ID/i)).not.toBeInTheDocument();
  expect(screen.queryByRole('region', { name: 'Planning period' })).not.toBeInTheDocument();
  expect(posts).toHaveLength(0);
});
test('select, validate, explicitly acknowledge and save exactly the validated payload', async () => {
  const { user, posts } = setup();
  const group = await fill();
  const preview = group.getByRole('link', { name: 'Inspect source page (opens new tab)' });
  expect(preview).toHaveAttribute('href', '/tenders/documents/doc-one/pages/2');
  expect(preview).toHaveAttribute('target', '_blank');
  expect(posts).toHaveLength(0);
  await validate(user);
  expect(posts).toHaveLength(1);
  expect(posts[0]?.operation).toBe('validate');
  expect(posts[0]?.intake.documents).toEqual([
    { document_id: 'doc-one', expected_pdf_sha256: sources[0]!.sha256 },
  ]);
  expect(posts[0]?.intake.findings[0]?.evidence).toEqual([
    { document_id: 'doc-one', page_number: 2, quote },
  ]);
  expect(
    screen.getByText(
      'Validation did not save an analysis. Inspect your interpretations before saving.',
    ),
  ).toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Save requirements' })).toBeDisabled();
  await user.click(screen.getByRole('button', { name: 'Save requirements' }));
  expect(posts).toHaveLength(1);
  await confirmAndSave(user);
  expect(await screen.findByRole('region', { name: 'Saved intake' })).toHaveTextContent(
    'New findings are UNREVIEWED.',
  );
  expect(posts[1]?.body).toEqual({ intake: posts[0]?.intake, confirm_manual_intake: true });
  const id = summary(posts[0]!.intake).analysis_id;
  expect(screen.getByRole('link', { name: 'Open analysis' })).toHaveAttribute(
    'href',
    `/tenders/analyses/${id}`,
  );
  expect(screen.getByRole('link', { name: 'Current findings' })).toHaveAttribute(
    'href',
    `/tenders/analyses/${id}#current-findings`,
  );
});
test('add/remove keeps stable item identities and the exact referenced inventory', async () => {
  const { user, posts } = setup();
  await fill();
  await user.click(screen.getByRole('button', { name: 'Add requirement' }));
  await fill(2, 'doc-two');
  await validate(user);
  expect(posts[0]?.intake.findings.map((f) => f.item_id)).toEqual(['item-1', 'item-2']);
  expect(posts[0]?.intake.documents).toHaveLength(2);
  await user.click(screen.getByRole('button', { name: 'Remove requirement 1' }));
  expect(screen.queryByRole('checkbox')).not.toBeInTheDocument();
  await user.click(screen.getByRole('button', { name: 'Add requirement' }));
  await fill(2, 'doc-two');
  await validate(user);
  expect(posts[1]?.intake.findings.map((f) => f.item_id)).toEqual(['item-2', 'item-3']);
  expect(posts[1]?.intake.documents).toEqual([
    { document_id: 'doc-two', expected_pdf_sha256: sources[1]!.sha256 },
  ]);
});
test.each([
  ['Intake purpose', 'Changed purpose'],
  ['Requirement statement', 'Changed requirement'],
  ['Category', 'RISK'],
  ['Requirement type', 'SECURITY'],
  ['Source document', 'doc-two'],
  ['Physical PDF page', '1'],
  ['Exact supporting quotation', 'Changed quote'],
  ['Interpretation note (optional)', 'New manual note'],
])('editing %s clears validation and confirmation without another POST', async (label, value) => {
  const { user, posts } = setup();
  const group = await fill();
  await validate(user);
  await user.click(screen.getByRole('checkbox'));
  const field =
    label === 'Intake purpose' ? screen.getByLabelText(label) : group.getByLabelText(label);
  fireEvent.change(field, { target: { value } });
  expect(screen.queryByRole('region', { name: 'Validated intake' })).not.toBeInTheDocument();
  expect(screen.queryByRole('button', { name: 'Save requirements' })).not.toBeInTheDocument();
  expect(posts).toHaveLength(1);
});
test('an out-of-date validation response cannot restore approval after an edit', async () => {
  let finish!: (response: Response) => void;
  const pending = new Promise<Response>((resolve) => {
    finish = resolve;
  });
  const { user, posts } = setup(() => pending);
  await fill();
  await user.click(screen.getByRole('button', { name: 'Validate' }));
  expect(screen.getByLabelText('Intake purpose')).toBeDisabled();
  fireEvent.change(screen.getByLabelText('Intake purpose'), { target: { value: 'Newer draft' } });
  await act(async () => finish(Response.json(summary(posts[0]!.intake))));
  expect(screen.queryByRole('region', { name: 'Validated intake' })).not.toBeInTheDocument();
  expect(screen.queryByRole('checkbox')).not.toBeInTheDocument();
  expect(posts).toHaveLength(1);
});
test('ALREADY_PRESENT is an explicit successful reuse without changing review claims', async () => {
  const { user } = setup((post) =>
    Response.json(
      summary(post.intake, post.operation === 'commit' ? 'ALREADY_PRESENT' : 'VALIDATED'),
    ),
  );
  await fill();
  await validate(user);
  await confirmAndSave(user);
  expect(await screen.findByRole('region', { name: 'Saved intake' })).toHaveTextContent(
    'ALREADY_PRESENT: existing reviews remain unchanged.',
  );
});
test.each([
  [422, 'SOURCE_QUOTE_NOT_VERIFIED'],
  [409, 'SOURCE_PDF_HASH_MISMATCH'],
  [404, 'SOURCE_DOCUMENT_NOT_FOUND'],
  [422, 'INVALID_CURATED_INTAKE'],
  [413, 'INTAKE_TOO_LARGE'],
])('validation HTTP %s shows only a safe %s code', async (status, code) => {
  const { user, posts } = setup(() =>
    Response.json({ detail: { code, message: 'PRIVATE' } }, { status }),
  );
  await fill();
  await user.click(screen.getByRole('button', { name: 'Validate' }));
  expect(await screen.findByRole('alert')).toHaveTextContent(String(code));
  expect(screen.queryByText(/PRIVATE/)).not.toBeInTheDocument();
  expect(screen.queryByRole('button', { name: 'Save requirements' })).not.toBeInTheDocument();
  expect(posts).toHaveLength(1);
});
test.each(['network', 'HTTP', 'invalid-response', 'wrong-identity'])(
  'interrupted commit (%s) preserves uncertainty and never retries',
  async (kind) => {
    const { user, posts } = setup((post) => {
      if (post.operation === 'validate') return Response.json(summary(post.intake));
      if (kind === 'network') throw Error('PRIVATE');
      if (kind === 'HTTP')
        return Response.json({ detail: { code: 'PRIVATE', message: 'PRIVATE' } }, { status: 504 });
      if (kind === 'invalid-response') return new Response('<html>PRIVATE</html>');
      return Response.json({
        ...summary(post.intake, 'CREATED'),
        analysis_id: `curated-${'e'.repeat(64)}`,
        intake_content_hash: `sha256:${'e'.repeat(64)}`,
      });
    });
    await fill();
    await validate(user);
    await confirmAndSave(user);
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'A failed response does not prove nothing was saved',
    );
    expect(
      screen.getByRole('link', { name: 'Inspect possible analysis (opens new tab)' }),
    ).toHaveAttribute('href', `/tenders/analyses/curated-${'d'.repeat(64)}`);
    expect(screen.queryByText(/PRIVATE/)).not.toBeInTheDocument();
    expect(screen.queryByRole('region', { name: 'Saved intake' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Save requirements' })).not.toBeInTheDocument();
    expect(posts.map((post) => post.operation)).toEqual(['validate', 'commit']);
  },
);
test('pending commit locks editing and cannot submit a duplicate POST', async () => {
  let finish!: (response: Response) => void;
  const pending = new Promise<Response>((resolve) => {
    finish = resolve;
  });
  const { user, posts } = setup((post) =>
    post.operation === 'validate' ? Response.json(summary(post.intake)) : pending,
  );
  await fill();
  await validate(user);
  await confirmAndSave(user);
  expect(screen.getByLabelText('Intake purpose')).toBeDisabled();
  fireEvent.submit(screen.getByRole('button', { name: 'Validate' }).closest('form')!);
  expect(posts.map((post) => post.operation)).toEqual(['validate', 'commit']);
  await act(async () => finish(Response.json(summary(posts[1]!.intake, 'CREATED'))));
  expect(await screen.findByRole('region', { name: 'Saved intake' })).toBeInTheDocument();
});
test('unconfigured backend never loads the editor or makes requests', () => {
  const { fetcher } = setup(undefined, '/tenders/intake', false);
  expect(screen.getByText(/TenderCite is not configured/)).toBeInTheDocument();
  expect(screen.queryByLabelText('Intake purpose')).not.toBeInTheDocument();
  expect(fetcher).not.toHaveBeenCalled();
});

test.each(['unavailable', 'invalid-catalogue', 'empty'])(
  'source catalogue %s keeps the editor unavailable safely',
  async (kind) => {
    setup(undefined, '/tenders/intake', true, () =>
      kind === 'unavailable'
        ? Response.json({ detail: 'PRIVATE' }, { status: 503 })
        : Response.json(kind === 'empty' ? [] : [{ ...sources[0], sha256: 'PRIVATE' }]),
    );
    if (kind === 'empty')
      expect(await screen.findByText(/No uploaded PDFs available/)).toBeInTheDocument();
    else
      expect(await screen.findByRole('alert')).toHaveTextContent(
        kind === 'unavailable' ? 'HTTP 503' : 'Invalid TenderCite document catalogue',
      );
    expect(screen.queryByLabelText('Intake purpose')).not.toBeInTheDocument();
    expect(screen.queryByText(/PRIVATE/)).not.toBeInTheDocument();
  },
);
