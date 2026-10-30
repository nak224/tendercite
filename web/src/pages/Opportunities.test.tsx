import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, expect, test, vi } from 'vitest';
import { App } from '../App';
import { opportunityRecord } from '../test/opportunities';
import type { NativeOpportunity, OpportunityRecord } from '../api/opportunities';

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  window.history.replaceState({}, '', '/');
});
type Write = {
  method: string;
  body: {
    schema_version: string;
    opportunity_id?: string;
    expected_revision?: number;
    opportunity: NativeOpportunity;
  };
};
function setup(
  options: {
    records?: OpportunityRecord[];
    configured?: boolean;
    listError?: boolean;
    write?: (entry: Write, persist: () => OpportunityRecord) => Response | Promise<Response>;
  } = {},
) {
  const records = new Map(
    (options.records ?? []).map((r) => [r.opportunity_id, structuredClone(r)]),
  );
  const writes: Write[] = [];
  const fetcher = vi.fn(async (request: Request) => {
    const url = new URL(request.url);
    expect(url.origin).toBe(window.location.origin);
    expect(url.pathname).toMatch(/^\/tendercite-api\/api\/v1\/opportunities(?:\/[a-f0-9-]+)?$/);
    const id = url.pathname.split('/').at(-1)!;
    if (request.method === 'GET') {
      if (url.pathname.endsWith('/opportunities'))
        return options.listError
          ? Response.json({ detail: { code: 'OPPORTUNITIES_API_DISABLED' } }, { status: 404 })
          : Response.json({
              schema_version: 'native-opportunity-listing-1',
              next_offset: null,
              opportunities: [...records.values()].map((r) => ({
                opportunity_id: r.opportunity_id,
                revision: r.revision,
                title: r.opportunity.title,
                opportunity_type: r.opportunity.opportunity_type,
                organization: r.opportunity.organization,
                deadline: r.opportunity.deadline,
                updated_at: r.updated_at,
              })),
            });
      return records.has(id)
        ? Response.json(records.get(id))
        : Response.json({ detail: { code: 'OPPORTUNITY_NOT_FOUND' } }, { status: 404 });
    }
    const entry = { method: request.method, body: await request.json() } as Write;
    writes.push(entry);
    const persist = () => {
      const key = entry.body.opportunity_id ?? id;
      const record = {
        ...opportunityRecord(),
        opportunity_id: key,
        revision: entry.method === 'POST' ? 1 : entry.body.expected_revision! + 1,
        opportunity: entry.body.opportunity,
      };
      records.set(key, structuredClone(record));
      return record;
    };
    return options.write
      ? options.write(entry, persist)
      : Response.json(
          {
            schema_version: 'native-opportunity-save-result-1',
            status: request.method === 'POST' ? 'CREATED' : 'UPDATED',
            record: persist(),
          },
          { status: request.method === 'POST' ? 201 : 200 },
        );
  });
  vi.stubGlobal('fetch', fetcher);
  vi.stubGlobal('__TENDERCITE_CONFIGURED__', options.configured ?? true);
  window.history.replaceState({}, '', '/opportunities');
  render(<App />);
  return { records, writes, fetcher, user: userEvent.setup() };
}
async function create(user: ReturnType<typeof userEvent.setup>) {
  await screen.findByText('No saved opportunities yet.');
  await user.click(screen.getByRole('button', { name: 'Create Opportunity' }));
  await user.type(screen.getByLabelText('Opportunity title'), 'Synthetic Python project');
}
const save = () => screen.getByRole('button', { name: 'Save Opportunity' });
test('complete manual workflow preserves all optional fields, plain skill labels and source references', async () => {
  const { user, writes } = setup();
  expect(screen.getByRole('link', { name: 'Opportunities' })).toHaveAttribute(
    'href',
    '/opportunities',
  );
  await create(user);
  await user.type(screen.getByLabelText('Organization or client (optional)'), 'Synthetic client');
  await user.type(
    screen.getByLabelText('Short description (optional)'),
    'Self-authored project example',
  );
  await user.type(screen.getByLabelText('Location (optional)'), 'Synthetic city');
  await user.selectOptions(screen.getByLabelText('Work arrangement'), 'REMOTE');
  fireEvent.change(screen.getByLabelText('Application/submission deadline (date only, optional)'), {
    target: { value: '2027-03-01' },
  });
  await user.type(
    screen.getByLabelText('Source platform or organization (optional)'),
    'Synthetic platform',
  );
  await user.type(
    screen.getByLabelText('Original opportunity URL (optional)'),
    'https://example.invalid/opportunity?ref=manual#details',
  );
  for (const [i, label] of ['Python', 'FastAPI'].entries()) {
    await user.click(screen.getByRole('button', { name: 'Add required skill' }));
    await user.type(screen.getByLabelText(`Skill ${i + 1} name`), label);
  }
  await user.type(
    screen.getByLabelText('Requirements description (optional)'),
    'Manually supplied, not legally interpreted.',
  );
  expect(writes).toHaveLength(0);
  expect(screen.getByText(/Unsaved changes/)).toBeInTheDocument();
  await user.click(save());
  await screen.findByText(/Server confirmed CREATED/);
  expect(writes[0]!.body.schema_version).toBe('native-opportunity-create-1');
  expect(writes[0]!.body.opportunity).toEqual(opportunityRecord().opportunity);
  expect(screen.queryByText(/Unsaved changes/)).not.toBeInTheDocument();
  expect(screen.getByText(/Deadline:/)).toHaveTextContent('2027-03-01');
  const source = screen.getByRole('link', { name: 'Open original opportunity' });
  expect(source).toHaveAttribute('href', opportunityRecord().opportunity.source_url);
  expect(source).toHaveAttribute('rel', 'noopener noreferrer');
  await user.click(screen.getByRole('button', { name: 'Open Synthetic Python project' }));
  expect(screen.getByLabelText('Skill 2 name')).toHaveValue('FastAPI');
  await user.clear(screen.getByLabelText('Opportunity title'));
  await user.type(screen.getByLabelText('Opportunity title'), 'Synthetic revised project');
  await user.click(screen.getByRole('button', { name: 'Remove skill 1' }));
  await user.clear(screen.getByLabelText('Skill 1 name'));
  await user.type(screen.getByLabelText('Skill 1 name'), 'React');
  await user.click(save());
  await screen.findByText(/Server confirmed UPDATED/);
  expect(writes[1]!.method).toBe('PUT');
  expect(writes[1]!.body.expected_revision).toBe(1);
  expect(writes[1]!.body.opportunity.required_skills).toEqual(['React']);
  expect(screen.getByText(/Saved revision: 2/)).toBeInTheDocument();
});
test.each(['PUBLIC_TENDER', 'OTHER'] as const)(
  '%s creates an independent manual opportunity without implying analysis',
  async (kind) => {
    const { user, writes } = setup();
    await create(user);
    await user.selectOptions(screen.getByLabelText('Opportunity type'), kind);
    await user.click(save());
    await screen.findByText(/Server confirmed CREATED/);
    expect(writes[0]!.body.opportunity.opportunity_type).toBe(kind);
    expect(writes[0]!.body.opportunity.required_skills).toEqual([]);
    expect(writes[0]!.body.opportunity.verification_status).toBe('NOT_VERIFIED');
    expect(screen.getByText(/not listings hosted or published/)).toHaveTextContent(
      'PUBLIC_TENDER opportunity is not an analyzed tender',
    );
  },
);
test('multiple independent records are preserved and saved records reopen without warnings', async () => {
  const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false);
  const { user, records } = setup();
  await create(user);
  await user.click(save());
  await screen.findByText(/Server confirmed CREATED/);
  await user.click(screen.getByRole('button', { name: 'Create Opportunity' }));
  await user.type(screen.getByLabelText('Opportunity title'), 'Another synthetic opportunity');
  await user.selectOptions(screen.getByLabelText('Opportunity type'), 'OTHER');
  await user.click(save());
  await screen.findByRole('button', { name: 'Open Another synthetic opportunity' });
  expect(records.size).toBe(2);
  await user.click(screen.getByRole('button', { name: 'Open Synthetic Python project' }));
  expect(screen.getByLabelText('Opportunity title')).toHaveValue('Synthetic Python project');
  expect(confirm).not.toHaveBeenCalled();
});
test('unsaved changes require confirmation before opening another record or creating a new one', async () => {
  const first = opportunityRecord();
  const second = {
    ...opportunityRecord(),
    opportunity_id: crypto.randomUUID(),
    opportunity: { ...opportunityRecord().opportunity, title: 'Another synthetic opportunity' },
  };
  const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false);
  const { user, writes } = setup({ records: [first, second] });
  await user.click(await screen.findByRole('button', { name: 'Open Synthetic Python project' }));
  await user.type(screen.getByLabelText('Opportunity title'), ' unsaved');
  await user.click(screen.getByRole('button', { name: 'Open Another synthetic opportunity' }));
  expect(screen.getByLabelText('Opportunity title')).toHaveValue(
    'Synthetic Python project unsaved',
  );
  await user.click(screen.getByRole('button', { name: 'Create Opportunity' }));
  expect(screen.getByLabelText('Opportunity title')).toHaveValue(
    'Synthetic Python project unsaved',
  );
  expect(confirm).toHaveBeenCalledTimes(2);
  expect(writes).toHaveLength(0);
  const unload = new Event('beforeunload', { cancelable: true });
  window.dispatchEvent(unload);
  expect(unload.defaultPrevented).toBe(true);
  confirm.mockReturnValue(true);
  await user.click(screen.getByRole('button', { name: 'Open Another synthetic opportunity' }));
  expect(screen.getByLabelText('Opportunity title')).toHaveValue('Another synthetic opportunity');
  expect(screen.queryByText(/Unsaved changes/)).not.toBeInTheDocument();
});
test.each(['duplicate', 'url'] as const)(
  'invalid %s input is not submitted and stays editable',
  async (kind) => {
    const { user, writes } = setup();
    await create(user);
    if (kind === 'url')
      await user.type(
        screen.getByLabelText('Original opportunity URL (optional)'),
        'javascript:alert(1)',
      );
    else
      for (let i = 1; i <= 2; i++) {
        await user.click(screen.getByRole('button', { name: 'Add required skill' }));
        await user.type(screen.getByLabelText(`Skill ${i} name`), i === 1 ? 'Python' : 'PYTHON');
      }
    await user.click(save());
    expect(await screen.findByRole('alert')).toHaveTextContent(/Enter a title/);
    expect(writes).toHaveLength(0);
    expect(screen.getByLabelText('Opportunity title')).toHaveValue('Synthetic Python project');
  },
);
test('revision conflict preserves form data and old revision until explicit adoption of saved state', async () => {
  const existing = opportunityRecord();
  vi.spyOn(window, 'confirm').mockReturnValue(true);
  const { user, writes, records } = setup({
    records: [existing],
    write: () => {
      records.set(existing.opportunity_id, {
        ...existing,
        revision: 2,
        opportunity: { ...existing.opportunity, title: 'Other saved edit' },
      });
      return Response.json(
        { detail: { code: 'OPPORTUNITY_REVISION_CONFLICT', message: 'PRIVATE' } },
        { status: 409 },
      );
    },
  });
  await user.click(await screen.findByRole('button', { name: 'Open Synthetic Python project' }));
  await user.clear(screen.getByLabelText('Short description (optional)'));
  await user.type(screen.getByLabelText('Short description (optional)'), 'My retained edits');
  await user.click(save());
  expect(await screen.findByRole('alert')).toHaveTextContent('OPPORTUNITY_REVISION_CONFLICT');
  await screen.findByRole('complementary', { name: 'Current saved version' });
  expect(screen.getByLabelText('Short description (optional)')).toHaveValue('My retained edits');
  expect(screen.getByText(/Saved revision: 1/)).toBeInTheDocument();
  expect(writes).toHaveLength(1);
  expect(document.body.textContent).not.toContain('PRIVATE');
  await user.click(
    screen.getByRole('button', { name: 'Load current saved version (discard my edits)' }),
  );
  expect(screen.getByLabelText('Opportunity title')).toHaveValue('Other saved edit');
  expect(screen.getByText(/Saved revision: 2/)).toBeInTheDocument();
});
test.each(['network', 'malformed', 'http'] as const)(
  '%s failure retains edits and checks saved state without retrying',
  async (kind) => {
    const { user, writes } = setup({
      write: (_entry, persist) => {
        if (kind !== 'http') persist();
        if (kind === 'network') throw Error('PRIVATE');
        return kind === 'malformed'
          ? new Response('PRIVATE', { headers: { 'Content-Type': 'application/json' } })
          : Response.json({ detail: 'PRIVATE' }, { status: 503 });
      },
    });
    await create(user);
    await user.click(save());
    expect(await screen.findByRole('alert')).toHaveTextContent('Save not confirmed');
    await waitFor(() => expect(save()).toBeEnabled());
    expect(writes).toHaveLength(1);
    expect(screen.getByLabelText('Opportunity title')).toHaveValue('Synthetic Python project');
    expect(screen.queryByText(/Server confirmed/)).not.toBeInTheDocument();
    expect(document.body.textContent).not.toContain('PRIVATE');
    if (kind !== 'http')
      expect(
        screen.getByRole('complementary', { name: 'Current saved version' }),
      ).toHaveTextContent('revision 1');
  },
);
test('pending save blocks overlapping submissions and opportunity switches', async () => {
  let finish!: (response: Response) => void;
  const pending = new Promise<Response>((resolve) => {
    finish = resolve;
  });
  let record: OpportunityRecord;
  const { user, writes } = setup({
    write: (_entry, persist) => {
      record = persist();
      return pending;
    },
  });
  await create(user);
  fireEvent.click(save());
  fireEvent.click(save());
  await waitFor(() => expect(writes).toHaveLength(1));
  expect(screen.getByLabelText('Opportunity title')).toBeDisabled();
  expect(screen.getByRole('button', { name: 'Create Opportunity' })).toBeDisabled();
  await act(async () =>
    finish(
      Response.json({
        schema_version: 'native-opportunity-save-result-1',
        status: 'CREATED',
        record: record!,
      }),
    ),
  );
  await screen.findByText(/Server confirmed CREATED/);
  expect(writes).toHaveLength(1);
});
test('already-present outcome confirms stored data without verification claims', async () => {
  const { user } = setup({
    write: (_entry, persist) =>
      Response.json({
        schema_version: 'native-opportunity-save-result-1',
        status: 'ALREADY_PRESENT',
        record: persist(),
      }),
  });
  await create(user);
  await user.click(save());
  expect(await screen.findByText(/Server confirmed ALREADY_PRESENT/)).toHaveTextContent(
    'Details remain unverified',
  );
});
test('disabled API gives a safe error and independent configuration instruction', async () => {
  const { writes } = setup({ listError: true });
  expect(await screen.findByRole('alert')).toHaveTextContent('OPPORTUNITIES_API_DISABLED');
  expect(screen.getByText(/TENDERCITE_OPPORTUNITIES_API_ENABLED/)).toBeInTheDocument();
  expect(writes).toHaveLength(0);
});
test('unconfigured page never contacts a backend', () => {
  const { fetcher } = setup({ configured: false });
  expect(screen.getByText(/TenderCite is not configured/)).toBeInTheDocument();
  expect(fetcher).not.toHaveBeenCalled();
});
