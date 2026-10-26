import { act, fireEvent, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, expect, test, vi } from 'vitest';
import { App } from '../App';
import { snapshotExample } from '../test/snapshot';
import { snapshotFile, snapshotFileText, snapshotSummary } from '../test/snapshotImport';

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  window.history.replaceState({}, '', '/');
});
function setup(
  reply?: (request: Request) => Response | Promise<Response>,
  path = '/tenders/workforce-snapshot/import',
  configured = true,
) {
  const fetcher = vi.fn(async (request: Request) => {
    if (reply) return reply(request);
    return Response.json(
      snapshotSummary(request.url.endsWith('/import') ? 'INSERTED' : 'VALIDATED'),
    );
  });
  vi.stubGlobal('fetch', fetcher);
  vi.stubGlobal('__TENDERCITE_CONFIGURED__', configured);
  window.history.replaceState({}, '', path);
  render(<App />);
  return { fetcher, user: userEvent.setup() };
}
async function select(user: ReturnType<typeof userEvent.setup>) {
  await user.upload(screen.getByLabelText('Capability snapshot JSON'), snapshotFile());
  await screen.findByText(/Selected JSON: \d+ bytes/);
}
async function validate(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByRole('button', { name: 'Validate Snapshot' }));
  await screen.findByText('Validation passed — not imported');
}
async function acknowledgeAndImport(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByRole('checkbox'));
  await user.click(screen.getByRole('button', { name: 'Import Snapshot' }));
}
test('standalone navigation opens import; selection alone makes no requests', async () => {
  const { fetcher, user } = setup(undefined, '/missing');
  await user.click(screen.getByRole('link', { name: 'Import snapshot' }));
  expect(screen.getByRole('link', { name: 'Import Workforce Snapshot' })).toHaveAttribute(
    'href',
    '/tenders/workforce-snapshot/import',
  );
  expect(screen.getByRole('heading', { name: 'Import Workforce Snapshot' })).toBeInTheDocument();
  expect(screen.getByText(/another compatible schema 1.0 producer/)).toBeInTheDocument();
  expect(
    screen.queryByRole('button', { name: 'Generate capability snapshot' }),
  ).not.toBeInTheDocument();
  expect(screen.queryByRole('region', { name: 'Planning period' })).not.toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Validate Snapshot' })).toBeDisabled();
  await select(user);
  expect(screen.queryByRole('checkbox')).not.toBeInTheDocument();
  expect(fetcher).not.toHaveBeenCalled();
});
test('unconfigured proxy prevents import without requests', () => {
  const { fetcher } = setup(undefined, undefined, false);
  expect(screen.getByText(/TenderCite is not configured/)).toBeInTheDocument();
  expect(screen.queryByLabelText('Capability snapshot JSON')).not.toBeInTheDocument();
  expect(fetcher).not.toHaveBeenCalled();
});
test.each(['empty', 'extension', 'oversized'])(
  'invalid %s selection stays local and cannot validate',
  async (kind) => {
    const { fetcher } = setup();
    const read = vi.spyOn(FileReader.prototype, 'readAsArrayBuffer');
    const file = new File(
      [kind === 'empty' ? '' : '{}'],
      kind === 'extension' ? 'synthetic.pdf' : 'synthetic.json',
    );
    if (kind === 'oversized') Object.defineProperty(file, 'size', { value: 16 * 1024 * 1024 + 1 });
    fireEvent.change(screen.getByLabelText('Capability snapshot JSON'), {
      target: { files: [file] },
    });
    expect(await screen.findByRole('alert')).toHaveTextContent(/nonempty .json file.*16 MiB/);
    expect(screen.getByRole('button', { name: 'Validate Snapshot' })).toBeDisabled();
    expect(read).not.toHaveBeenCalled();
    expect(fetcher).not.toHaveBeenCalled();
  },
);
test('explicit validation displays metadata only and cannot import without acknowledgement', async () => {
  const { fetcher, user } = setup();
  await select(user);
  await validate(user);
  const summary = within(screen.getByRole('region', { name: 'Snapshot metadata' }));
  expect(summary.getByText(`Content hash: ${snapshotExample.content_hash}`)).toBeInTheDocument();
  expect(summary.getByText('Schema: 1.0 · Source: team-capacity-engine')).toBeInTheDocument();
  expect(summary.getByText('Status: VALIDATED')).toBeInTheDocument();
  expect(
    summary.getByText('Assessment period: 2027-02-01 through 2027-02-07 (inclusive)'),
  ).toBeInTheDocument();
  expect(summary.getByText('Members: 3 · Assignments: 3 · Catalog skills: 2')).toBeInTheDocument();
  for (const member of snapshotExample.workforce_members)
    expect(document.body.textContent).not.toContain(member.person_id);
  for (const skill of snapshotExample.skill_catalog)
    expect(document.body.textContent).not.toContain(skill.name);
  expect(screen.getByRole('checkbox')).not.toBeChecked();
  const importing = screen.getByRole('button', { name: 'Import Snapshot' });
  expect(importing).toBeDisabled();
  await user.click(importing);
  expect(fetcher).toHaveBeenCalledTimes(1);
  expect(fetcher.mock.calls[0]![0].url).toMatch(/\/capability-snapshots\/validate$/);
  expect(await fetcher.mock.calls[0]![0].text()).toBe(snapshotFileText);
});
test.each(['INSERTED', 'ALREADY_PRESENT'] as const)(
  'confirmed %s is metadata-only and resets approval for explicit repeats',
  async (status) => {
    const { fetcher, user } = setup((req) =>
      Response.json(snapshotSummary(req.url.endsWith('/import') ? status : 'VALIDATED')),
    );
    await select(user);
    await validate(user);
    await acknowledgeAndImport(user);
    expect(await screen.findByText(`Status: ${status}`)).toBeInTheDocument();
    expect(screen.getByText('Import confirmed')).toBeInTheDocument();
    expect(screen.getByText('Original import time: 2026-10-24T12:00:00Z')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Import Snapshot' })).not.toBeInTheDocument();
    expect(fetcher).toHaveBeenCalledTimes(2);
    const request = fetcher.mock.calls[1]![0];
    expect(await request.text()).toBe(snapshotFileText);
    expect(request.headers.get('X-TenderCite-Acknowledge-Local-Synthetic-Data')).toBe('true');
    expect(screen.getByLabelText('Capability snapshot JSON')).toHaveProperty('files.length', 1);
  },
);
test('changing or clearing the file invalidates validation and confirmation without requests', async () => {
  const { fetcher, user } = setup();
  await select(user);
  await validate(user);
  await user.click(screen.getByRole('checkbox'));
  const changed = new File(['\n' + snapshotFileText], 'different-synthetic.json', {
    type: 'application/json',
  });
  await user.upload(screen.getByLabelText('Capability snapshot JSON'), changed);
  await screen.findByText(`Selected JSON: ${changed.size} bytes.`);
  expect(screen.queryByRole('region', { name: 'Snapshot metadata' })).not.toBeInTheDocument();
  expect(screen.queryByRole('checkbox')).not.toBeInTheDocument();
  await validate(user);
  expect(screen.getByRole('checkbox')).not.toBeChecked();
  fireEvent.change(screen.getByLabelText('Capability snapshot JSON'), { target: { files: [] } });
  expect(screen.getByRole('button', { name: 'Validate Snapshot' })).toBeDisabled();
  expect(screen.queryByRole('checkbox')).not.toBeInTheDocument();
  expect(fetcher).toHaveBeenCalledTimes(2);
});
test.each([
  'HASH_MISMATCH',
  'UNSUPPORTED_SCHEMA',
  'INVALID_CONTRACT',
  'CAPABILITY_SNAPSHOT_API_DISABLED',
])('validation error %s never enables import or leaks response data', async (code) => {
  const { fetcher, user } = setup(() =>
    Response.json(
      { detail: { code, message: 'PRIVATE' } },
      { status: code === 'CAPABILITY_SNAPSHOT_API_DISABLED' ? 404 : 422 },
    ),
  );
  await select(user);
  await user.click(screen.getByRole('button', { name: 'Validate Snapshot' }));
  expect(await screen.findByRole('alert')).toHaveTextContent(code);
  expect(document.body.textContent).not.toContain('PRIVATE');
  expect(screen.queryByRole('checkbox')).not.toBeInTheDocument();
  expect(fetcher).toHaveBeenCalledTimes(1);
});
test.each(['HTTP', 'network', 'invalid-JSON', 'wrong-hash', 'wrong-counts'])(
  'uncertain import after %s preserves selection, clears approval and never retries',
  async (kind) => {
    const { fetcher, user } = setup((request) => {
      if (request.url.endsWith('/validate')) return Response.json(snapshotSummary());
      if (kind === 'network') throw Error('PRIVATE');
      if (kind === 'HTTP')
        return Response.json(
          { detail: { code: 'DATABASE_FAILED', message: 'PRIVATE' } },
          { status: 503 },
        );
      if (kind === 'invalid-JSON')
        return new Response('PRIVATE', { headers: { 'content-type': 'application/json' } });
      return Response.json({
        ...snapshotSummary('INSERTED'),
        ...(kind === 'wrong-hash'
          ? { content_hash: `sha256:${'0'.repeat(64)}` }
          : { member_count: 4 }),
      });
    });
    await select(user);
    await validate(user);
    await acknowledgeAndImport(user);
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Import outcome uncertain: the snapshot may already be stored.',
    );
    expect(screen.getByRole('alert')).toHaveTextContent('Explicitly validate it again');
    expect(document.body.textContent).not.toContain('PRIVATE');
    expect(screen.getByLabelText('Capability snapshot JSON')).toHaveProperty('files.length', 1);
    expect(screen.queryByRole('checkbox')).not.toBeInTheDocument();
    expect(screen.queryByText('Import confirmed')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Validate Snapshot' })).toBeEnabled();
    expect(fetcher).toHaveBeenCalledTimes(2);
  },
);
test('explicit revalidation and acknowledgement allow deduplicated repeat after a lost import response', async () => {
  let stored = false;
  const { fetcher, user } = setup((request) => {
    if (request.url.endsWith('/validate')) return Response.json(snapshotSummary());
    if (stored) return Response.json(snapshotSummary('ALREADY_PRESENT'));
    stored = true;
    throw Error('Lost response after persistence');
  });
  await select(user);
  await validate(user);
  await acknowledgeAndImport(user);
  await screen.findByRole('alert');
  expect(fetcher).toHaveBeenCalledTimes(2);
  await validate(user);
  expect(screen.getByRole('checkbox')).not.toBeChecked();
  await acknowledgeAndImport(user);
  expect(await screen.findByText('Status: ALREADY_PRESENT')).toBeInTheDocument();
  expect(fetcher).toHaveBeenCalledTimes(4);
});
test.each(['validate', 'import'] as const)(
  'pending %s prevents overlapping actions and file edits',
  async (operation) => {
    let finish!: (response: Response) => void;
    const pending = new Promise<Response>((resolve) => {
      finish = resolve;
    });
    const { fetcher, user } = setup((request) =>
      request.url.endsWith(`/${operation}`) ? pending : Response.json(snapshotSummary()),
    );
    await select(user);
    if (operation === 'import') {
      await validate(user);
      await user.click(screen.getByRole('checkbox'));
    }
    const button = screen.getByRole('button', {
      name: operation === 'validate' ? 'Validate Snapshot' : 'Import Snapshot',
    });
    fireEvent.click(button);
    fireEvent.click(button);
    fireEvent.click(screen.getByRole('button', { name: 'Validate Snapshot' }));
    expect(screen.getByLabelText('Capability snapshot JSON')).toBeDisabled();
    expect(fetcher).toHaveBeenCalledTimes(operation === 'validate' ? 1 : 2);
    await act(async () =>
      finish(Response.json(snapshotSummary(operation === 'validate' ? 'VALIDATED' : 'INSERTED'))),
    );
    expect(await screen.findByRole('region', { name: 'Snapshot metadata' })).toBeInTheDocument();
  },
);
test('late validation for an edited file cannot authorize the new selection', async () => {
  let finish!: (response: Response) => void;
  const pending = new Promise<Response>((resolve) => {
    finish = resolve;
  });
  const { user } = setup(() => pending);
  await select(user);
  await user.click(screen.getByRole('button', { name: 'Validate Snapshot' }));
  // Disabled in the UI; exercise the revision guard against programmatic changes too.
  fireEvent.change(screen.getByLabelText('Capability snapshot JSON'), {
    target: { files: [snapshotFile()] },
  });
  await act(async () => finish(Response.json(snapshotSummary())));
  expect(screen.queryByRole('checkbox')).not.toBeInTheDocument();
  expect(screen.queryByRole('region', { name: 'Snapshot metadata' })).not.toBeInTheDocument();
});

test('late import for an earlier file cannot confirm the current selection', async () => {
  let finish!: (response: Response) => void;
  const pending = new Promise<Response>((resolve) => {
    finish = resolve;
  });
  const { user } = setup((request) =>
    request.url.endsWith('/import') ? pending : Response.json(snapshotSummary()),
  );
  await select(user);
  await validate(user);
  await acknowledgeAndImport(user);
  fireEvent.change(screen.getByLabelText('Capability snapshot JSON'), {
    target: { files: [snapshotFile()] },
  });
  await act(async () => finish(Response.json(snapshotSummary('INSERTED'))));
  expect(await screen.findByRole('alert')).toHaveTextContent('An earlier file was submitted.');
  expect(screen.queryByText('Import confirmed')).not.toBeInTheDocument();
  expect(screen.queryByRole('checkbox')).not.toBeInTheDocument();
});
