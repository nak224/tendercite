import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, expect, test, vi } from 'vitest';
import { App } from '../App';
import { pipelineEntry, pipelineId } from '../test/pipeline';
import { opportunityMetadata } from '../test/nativeMatching';
import { pipelineStages, type PipelineEntry, type PipelineValues } from '../api/pipeline';

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  window.history.replaceState({}, '', '/');
});
type Write = {
  method: string;
  body: {
    pipeline_id?: string;
    opportunity_id?: string;
    expected_revision?: number;
    values?: PipelineValues;
    schema_version: string;
  };
};
function setup(
  options: {
    entries?: PipelineEntry[];
    configured?: boolean;
    disabled?: boolean;
    write?: (input: Write, persist: () => PipelineEntry) => Response | Promise<Response>;
    readFailure?: boolean;
  } = {},
) {
  const entries = new Map(
    (options.entries ?? []).map((e) => [e.record.pipeline_id, structuredClone(e)]),
  );
  const writes: Write[] = [];
  const reads: string[] = [];
  const fetcher = vi.fn(async (request: Request) => {
    const url = new URL(request.url);
    expect(url.origin).toBe(window.location.origin);
    if (url.pathname.endsWith('/opportunities'))
      return Response.json({
        schema_version: 'native-opportunity-listing-1',
        opportunities: [opportunityMetadata()],
        next_offset: null,
      });
    expect(url.pathname).toMatch(/^\/tendercite-api\/api\/v1\/pipeline(?:\/[a-f0-9-]+)?$/);
    if (options.disabled)
      return Response.json(
        { detail: { code: 'PIPELINE_API_DISABLED', PRIVATE: 'PRIVATE' } },
        { status: 404 },
      );
    if (request.method === 'GET') {
      reads.push(url.pathname + url.search);
      if (url.pathname.endsWith('/pipeline')) {
        const list = [...entries.values()].filter(
          (e) =>
            (!url.searchParams.get('stage') ||
              e.record.values.stage === url.searchParams.get('stage')) &&
            (!url.searchParams.get('opportunity_id') ||
              e.record.opportunity_id === url.searchParams.get('opportunity_id')),
        );
        return Response.json({
          schema_version: 'native-pipeline-listing-1',
          entries: list,
          next_offset: null,
        });
      }
      const entry = entries.get(url.pathname.split('/').at(-1)!);
      return entry && !options.readFailure
        ? Response.json(entry)
        : Response.json(
            { detail: { code: 'PIPELINE_NOT_FOUND', PRIVATE: 'PRIVATE' } },
            { status: 404 },
          );
    }
    const write = { method: request.method, body: await request.json() } as Write;
    writes.push(write);
    const persist = () => {
      if (request.method === 'POST') {
        const entry = pipelineEntry();
        entry.record.pipeline_id = write.body.pipeline_id!;
        entries.set(entry.record.pipeline_id, entry);
        return entry;
      }
      const id = url.pathname.split('/').at(-1)!;
      const entry = structuredClone(entries.get(id)!);
      entry.record.values = write.body.values!;
      entry.record.revision++;
      entries.set(id, entry);
      return entry;
    };
    return options.write
      ? options.write(write, persist)
      : Response.json({
          schema_version: 'native-pipeline-save-result-1',
          status: request.method === 'POST' ? 'CREATED' : 'UPDATED',
          entry: persist(),
        });
  });
  vi.stubGlobal('fetch', fetcher);
  vi.stubGlobal('__TENDERCITE_CONFIGURED__', options.configured ?? true);
  window.history.replaceState({}, '', '/pipeline');
  render(<App />);
  return { user: userEvent.setup(), writes, reads, fetcher, entries };
}
const editor = () => screen.getByRole('region', { name: 'Pipeline editor' });
const save = () => screen.getByRole('button', { name: 'Save Changes' });
async function open(user: ReturnType<typeof userEvent.setup>) {
  await user.click(await screen.findByRole('button', { name: 'Open Synthetic Python project' }));
  await screen.findByRole('region', { name: 'Pipeline editor' });
}
async function selectOpportunity(user: ReturnType<typeof userEvent.setup>) {
  const select = await screen.findByRole('combobox', { name: 'Existing opportunity' });
  await user.selectOptions(select, opportunityMetadata().opportunity_id);
  await waitFor(() =>
    expect(screen.getByRole('button', { name: 'Add to Pipeline' })).toBeEnabled(),
  );
}
test('explicit creation, default state, identities and no automatic matching or submission', async () => {
  const { user, writes, fetcher } = setup();
  await screen.findByText('No tracked opportunities in this view.');
  await selectOpportunity(user);
  expect(writes).toHaveLength(0);
  await user.click(screen.getByRole('button', { name: 'Add to Pipeline' }));
  await screen.findByRole('region', { name: 'Pipeline editor' });
  expect(writes).toHaveLength(1);
  expect(writes[0]!.body).toEqual({
    schema_version: 'native-pipeline-create-1',
    pipeline_id: expect.any(String),
    opportunity_id: opportunityMetadata().opportunity_id,
  });
  expect(screen.getByText(/Server confirmed CREATED/)).toBeVisible();
  expect(screen.getByRole('combobox', { name: 'Stage' })).toHaveValue('SAVED');
  expect(screen.getByRole('combobox', { name: 'Priority' })).toHaveValue('MEDIUM');
  expect(fetcher.mock.calls.every(([r]) => !r.url.includes('native-matches'))).toBe(true);
  expect(localStorage.length + sessionStorage.length).toBe(0);
});
test('already tracked selector offers existing record without another POST', async () => {
  const { user, writes } = setup({ entries: [pipelineEntry()] });
  await user.selectOptions(
    await screen.findByRole('combobox', { name: 'Existing opportunity' }),
    opportunityMetadata().opportunity_id,
  );
  await user.click(await screen.findByRole('button', { name: 'Open existing pipeline entry' }));
  expect(editor()).toHaveTextContent(pipelineId);
  expect(screen.getByRole('button', { name: 'Add to Pipeline' })).toBeDisabled();
  expect(writes).toHaveLength(0);
});
test('concurrent duplicate creation offers current existing identity without claiming a new record', async () => {
  const { user, writes } = setup({
    write: () =>
      Response.json({
        schema_version: 'native-pipeline-save-result-1',
        status: 'ALREADY_PRESENT',
        entry: pipelineEntry(),
      }),
  });
  await selectOpportunity(user);
  await user.click(screen.getByRole('button', { name: 'Add to Pipeline' }));
  expect(await screen.findByText(/Server confirmed ALREADY_PRESENT/)).toBeVisible();
  expect(screen.queryByRole('region', { name: 'Pipeline editor' })).not.toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Open existing pipeline entry' })).toBeVisible();
  expect(writes).toHaveLength(1);
});
test.each(pipelineStages)(
  'manual %s stage, notes, action and date are saved only explicitly',
  async (stage) => {
    const { user, writes } = setup({ entries: [pipelineEntry()] });
    await open(user);
    await user.selectOptions(screen.getByRole('combobox', { name: 'Stage' }), stage);
    await user.selectOptions(screen.getByRole('combobox', { name: 'Priority' }), 'HIGH');
    await user.type(
      screen.getByLabelText('Notes (optional, maximum 4000 characters)'),
      'Synthetic note',
    );
    await user.type(
      screen.getByLabelText('Next action (optional, maximum 1000 characters)'),
      'Prepare synthetic bid',
    );
    fireEvent.change(screen.getByLabelText('Next-action due date (optional, date only)'), {
      target: { value: '2027-02-15' },
    });
    expect(writes).toHaveLength(0);
    await user.click(save());
    expect(await screen.findByText(/Server confirmed UPDATED/)).toBeVisible();
    expect(writes).toEqual([
      {
        method: 'PUT',
        body: {
          schema_version: 'native-pipeline-update-1',
          expected_revision: 1,
          values: {
            stage,
            priority: 'HIGH',
            notes: 'Synthetic note',
            next_action: 'Prepare synthetic bid',
            next_action_due_date: '2027-02-15',
          },
        },
      },
    ]);
    expect(editor()).toHaveTextContent('Revision 2');
    expect(editor()).toHaveTextContent('2027-03-01'); // independent source submission deadline
  },
);
test('all priorities and clearing optional values persist without enforced transitions', async () => {
  const entry = pipelineEntry();
  entry.record.values.stage = 'WON';
  const { user, writes } = setup({ entries: [entry] });
  await open(user);
  for (const priority of ['LOW', 'MEDIUM', 'HIGH']) {
    await user.selectOptions(screen.getByRole('combobox', { name: 'Priority' }), priority);
    await user.selectOptions(screen.getByRole('combobox', { name: 'Stage' }), 'SAVED');
    await user.click(save());
    await waitFor(() => expect(save()).toBeEnabled());
  }
  expect(writes.map((w) => w.body.values!.priority)).toEqual(['LOW', 'MEDIUM', 'HIGH']);
});
test('stage filtering retains current unsaved editor and does not mutate progress', async () => {
  const { user, writes, reads } = setup({ entries: [pipelineEntry()] });
  await open(user);
  await user.type(screen.getByLabelText('Notes (optional, maximum 4000 characters)'), 'Unsaved');
  await user.selectOptions(screen.getByRole('combobox', { name: 'Filter by stage' }), 'WON');
  expect(await screen.findByText('No tracked opportunities in this view.')).toBeVisible();
  expect(screen.getByLabelText('Notes (optional, maximum 4000 characters)')).toHaveValue('Unsaved');
  expect(writes).toHaveLength(0);
  expect(reads.some((p) => p.includes('stage=WON'))).toBe(true);
});
test('failed conflict retains edits, reads authoritative state and requires explicit adoption', async () => {
  let conflict = true;
  const { user, writes, entries } = setup({
    entries: [pipelineEntry()],
    write: (_write, persist) => {
      if (conflict) {
        const entry = entries.get(pipelineId)!;
        entry.record.revision = 2;
        entry.record.values.notes = 'Other writer';
        return Response.json(
          { detail: { code: 'PIPELINE_REVISION_CONFLICT', PRIVATE: 'PRIVATE' } },
          { status: 409 },
        );
      }
      return Response.json({
        schema_version: 'native-pipeline-save-result-1',
        status: 'UPDATED',
        entry: persist(),
      });
    },
  });
  await open(user);
  await user.type(
    screen.getByLabelText('Notes (optional, maximum 4000 characters)'),
    'My unsaved note',
  );
  await user.click(save());
  expect(await screen.findByRole('alert')).toHaveTextContent('PIPELINE_REVISION_CONFLICT');
  expect(screen.getByRole('alert')).not.toHaveTextContent('PRIVATE');
  expect(
    await screen.findByRole('complementary', { name: 'Current saved pipeline entry' }),
  ).toHaveTextContent('Current server revision 2');
  expect(screen.getByLabelText('Notes (optional, maximum 4000 characters)')).toHaveValue(
    'My unsaved note',
  );
  expect(save()).toBeDisabled();
  expect(writes).toHaveLength(1);
  vi.spyOn(window, 'confirm').mockReturnValue(true);
  conflict = false;
  await user.click(
    screen.getByRole('button', { name: 'Load current saved entry (discard my edits)' }),
  );
  expect(screen.getByLabelText('Notes (optional, maximum 4000 characters)')).toHaveValue(
    'Other writer',
  );
  await user.click(save());
  await screen.findByText(/Server confirmed UPDATED/);
  expect(writes[1]!.body.expected_revision).toBe(2);
});
test.each(['network', 'http', 'invalid'])(
  'uncertain %s save preserves edits and never retries',
  async (kind) => {
    const { user, writes } = setup({
      entries: [pipelineEntry()],
      write: (_write, persist) => {
        persist();
        if (kind === 'network') throw new Error('PRIVATE');
        return kind === 'http'
          ? Response.json({ detail: { code: 'PRIVATE' } }, { status: 502 })
          : Response.json({ PRIVATE: 'PRIVATE' });
      },
    });
    await open(user);
    await user.type(
      screen.getByLabelText('Notes (optional, maximum 4000 characters)'),
      'Retained synthetic edit',
    );
    await user.click(save());
    expect(await screen.findByRole('alert')).toHaveTextContent('Save not confirmed');
    expect(screen.getByRole('alert')).not.toHaveTextContent('PRIVATE');
    await screen.findByRole('complementary', { name: 'Current saved pipeline entry' });
    expect(screen.getByLabelText('Notes (optional, maximum 4000 characters)')).toHaveValue(
      'Retained synthetic edit',
    );
    expect(save()).toBeDisabled();
    expect(writes).toHaveLength(1);
    expect(screen.queryByText(/Server confirmed UPDATED/)).not.toBeInTheDocument();
  },
);
test('uncertain create locates saved entry without claiming confirmation or repeating POST', async () => {
  const { user, writes } = setup({
    write: (_write, persist) => {
      persist();
      throw new Error('PRIVATE');
    },
  });
  await selectOpportunity(user);
  await user.click(screen.getByRole('button', { name: 'Add to Pipeline' }));
  expect(await screen.findByRole('alert')).toHaveTextContent('Creation not confirmed');
  expect(await screen.findByRole('button', { name: 'Open existing pipeline entry' })).toBeVisible();
  expect(writes).toHaveLength(1);
  expect(screen.queryByText(/Server confirmed CREATED/)).not.toBeInTheDocument();
});
test('overlapping Save Changes is prevented while the response is pending', async () => {
  let complete!: (response: Response) => void;
  const { user, writes } = setup({
    entries: [pipelineEntry()],
    write: () =>
      new Promise((resolve) => {
        complete = resolve;
      }),
  });
  await open(user);
  fireEvent.click(save());
  fireEvent.click(save());
  await waitFor(() => expect(writes).toHaveLength(1));
  expect(save()).toBeDisabled();
  const entry = pipelineEntry();
  entry.record.revision = 2;
  await act(async () =>
    complete(
      Response.json({ schema_version: 'native-pipeline-save-result-1', status: 'UPDATED', entry }),
    ),
  );
  await screen.findByText(/Server confirmed UPDATED/);
});
test('dirty internal selection and reload/unload require discard confirmation', async () => {
  const { user } = setup({ entries: [pipelineEntry()] });
  await open(user);
  await user.type(
    screen.getByLabelText('Notes (optional, maximum 4000 characters)'),
    'Private draft',
  );
  const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false);
  await user.click(screen.getByRole('button', { name: 'Open Synthetic Python project' }));
  expect(confirm).toHaveBeenCalled();
  expect(screen.getByLabelText('Notes (optional, maximum 4000 characters)')).toHaveValue(
    'Private draft',
  );
  const unload = new Event('beforeunload', { cancelable: true });
  window.dispatchEvent(unload);
  expect(unload.defaultPrevented).toBe(true);
});
test('SPA navigation warns and keeps edits until explicit discard', async () => {
  const { user } = setup({ entries: [pipelineEntry()] });
  await open(user);
  await user.type(screen.getByLabelText('Notes (optional, maximum 4000 characters)'), 'Unsaved');
  const nav = within(screen.getByRole('navigation', { name: 'Primary navigation' }));
  await user.click(nav.getByRole('link', { name: 'Opportunity Matching' }));
  expect(
    await screen.findByRole('alertdialog', { name: 'Unsaved pipeline changes' }),
  ).toBeVisible();
  expect(screen.getByRole('button', { name: 'Keep editing' })).toHaveFocus();
  await user.click(screen.getByRole('button', { name: 'Keep editing' }));
  expect(screen.getByLabelText('Notes (optional, maximum 4000 characters)')).toHaveValue('Unsaved');
  await user.click(nav.getByRole('link', { name: 'Opportunity Matching' }));
  await user.click(screen.getByRole('button', { name: 'Discard changes and leave' }));
  expect(await screen.findByRole('heading', { name: 'Opportunity Matching' })).toBeVisible();
});
test('disabled and unconfigured states are safe without automatic writes', async () => {
  const { writes } = setup({ disabled: true });
  expect((await screen.findAllByRole('alert'))[0]).toHaveTextContent('PIPELINE_API_DISABLED');
  expect(writes).toHaveLength(0);
});
test('unconfigured proxy makes no requests', () => {
  const { fetcher } = setup({ configured: false });
  expect(screen.getByText(/TenderCite is not configured/)).toBeVisible();
  expect(fetcher).not.toHaveBeenCalled();
});
