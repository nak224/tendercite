import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, expect, test, vi } from 'vitest';
import { App } from '../App';
import { profileRecord } from '../test/profiles';
import type { NativeProfile, ProfileRecord } from '../api/profiles';

afterEach(() => {
  vi.unstubAllGlobals();
  window.history.replaceState({}, '', '/');
});
type Write = {
  method: string;
  body: {
    schema_version: string;
    profile_id?: string;
    expected_revision?: number;
    profile: NativeProfile;
  };
};
function setup(
  options: {
    records?: ProfileRecord[];
    configured?: boolean;
    listError?: boolean;
    write?: (entry: Write, persist: () => ProfileRecord) => Response | Promise<Response>;
  } = {},
) {
  const records = new Map((options.records ?? []).map((r) => [r.profile_id, structuredClone(r)]));
  const writes: Write[] = [];
  const fetcher = vi.fn(async (request: Request) => {
    const url = new URL(request.url);
    expect(url.origin).toBe(window.location.origin);
    expect(url.pathname).toMatch(/^\/tendercite-api\/api\/v1\/profiles(?:\/[a-f0-9-]+)?$/);
    const id = url.pathname.split('/').at(-1)!;
    if (request.method === 'GET') {
      if (url.pathname.endsWith('/profiles')) {
        if (options.listError)
          return Response.json({ detail: { code: 'PROFILES_API_DISABLED' } }, { status: 404 });
        return Response.json({
          schema_version: 'native-profile-listing-1',
          next_offset: null,
          profiles: [...records.values()].map((r) => ({
            profile_id: r.profile_id,
            revision: r.revision,
            name: r.profile.name,
            profile_type: r.profile.profile_type,
            updated_at: r.updated_at,
          })),
        });
      }
      return records.has(id)
        ? Response.json(records.get(id))
        : Response.json({ detail: { code: 'PROFILE_NOT_FOUND' } }, { status: 404 });
    }
    const entry = { method: request.method, body: await request.json() } as Write;
    writes.push(entry);
    const persist = () => {
      const key = entry.body.profile_id ?? id;
      const value = {
        ...profileRecord(),
        profile_id: key,
        revision: entry.method === 'POST' ? 1 : entry.body.expected_revision! + 1,
        profile: entry.body.profile,
      };
      records.set(key, structuredClone(value));
      return value;
    };
    return options.write
      ? options.write(entry, persist)
      : Response.json(
          {
            schema_version: 'native-profile-save-result-1',
            status: request.method === 'POST' ? 'CREATED' : 'UPDATED',
            record: persist(),
          },
          { status: request.method === 'POST' ? 201 : 200 },
        );
  });
  vi.stubGlobal('fetch', fetcher);
  vi.stubGlobal('__TENDERCITE_CONFIGURED__', options.configured ?? true);
  window.history.replaceState({}, '', '/profiles');
  render(<App />);
  return { records, writes, fetcher, user: userEvent.setup() };
}
async function create(user: ReturnType<typeof userEvent.setup>, name = 'Synthetic freelancer') {
  await screen.findByText('No saved profiles yet.');
  await user.click(screen.getByRole('button', { name: 'Create profile' }));
  await user.type(screen.getByLabelText('Profile name'), name);
}
const save = () => screen.getByRole('button', { name: 'Save profile' });

test('complete freelancer workflow creates five manual skills, reopens, edits and removes them', async () => {
  const { user, writes } = setup();
  expect(screen.getByRole('link', { name: 'Profiles' })).toHaveAttribute('href', '/profiles');
  await create(user);
  await user.type(
    screen.getByLabelText('Headline or short description'),
    'Synthetic software services',
  );
  await user.type(screen.getByLabelText('Location (optional)'), 'Synthetic city');
  await user.selectOptions(screen.getByLabelText('Preferred work arrangement'), 'REMOTE');
  for (const [index, name] of [
    'Python',
    'Machine Learning',
    'Computer Vision',
    'React',
    'Project Management',
  ].entries()) {
    await user.click(screen.getByRole('button', { name: 'Add skill' }));
    await user.type(screen.getByLabelText(`Skill ${index + 1} name`), name);
  }
  await user.type(
    screen.getByLabelText('Opportunity keywords (optional, one per line)'),
    'Software Development\nConsulting',
  );
  expect(writes).toHaveLength(0);
  await user.click(save());
  await screen.findByText(/Server confirmed CREATED/);
  const request = writes[0]!.body;
  expect(request.schema_version).toBe('native-profile-create-1');
  expect(request.profile.skills.map((s) => s.name)).toEqual([
    'Python',
    'Machine Learning',
    'Computer Vision',
    'React',
    'Project Management',
  ]);
  expect(new Set(request.profile.skills.map((s) => s.skill_id)).size).toBe(5);
  expect(
    request.profile.skills.every(
      (s) =>
        s.source_origin === 'manual_operator_entry' && s.verification_status === 'NOT_VERIFIED',
    ),
  ).toBe(true);
  expect(request.profile.opportunity_keywords).toEqual(['Software Development', 'Consulting']);
  await user.click(await screen.findByRole('button', { name: 'Open Synthetic freelancer' }));
  expect(screen.getByLabelText('Skill 3 name')).toHaveValue('Computer Vision');
  await user.clear(screen.getByLabelText('Profile name'));
  await user.type(screen.getByLabelText('Profile name'), 'Synthetic updated freelancer');
  await user.click(screen.getByRole('button', { name: 'Remove skill 2' }));
  await user.clear(screen.getByLabelText('Skill 1 name'));
  await user.type(screen.getByLabelText('Skill 1 name'), 'Python development');
  expect(writes).toHaveLength(1);
  await user.click(save());
  await screen.findByText(/Server confirmed UPDATED/);
  expect(writes[1]!.method).toBe('PUT');
  expect(writes[1]!.body.expected_revision).toBe(1);
  expect(writes[1]!.body.profile.skills).toHaveLength(4);
  expect(writes[1]!.body.profile.skills[0]!.skill_id).toBe(request.profile.skills[0]!.skill_id);
  expect(screen.getByText(/Saved revision: 2/)).toBeInTheDocument();
});
test('company and multiple profiles save independently without imported data', async () => {
  const { user, writes, records } = setup();
  await create(user);
  await user.click(save());
  await screen.findByText(/Server confirmed CREATED/);
  await user.click(screen.getByRole('button', { name: 'Create profile' }));
  await user.type(screen.getByLabelText('Profile name'), 'Synthetic company');
  await user.selectOptions(screen.getByLabelText('Profile type'), 'COMPANY');
  await user.selectOptions(screen.getByLabelText('Preferred work arrangement'), 'ON_SITE');
  await user.click(save());
  await screen.findByRole('button', { name: 'Open Synthetic company' });
  expect(writes[1]!.body.profile.profile_type).toBe('COMPANY');
  expect(records.size).toBe(2);
  await user.click(screen.getByRole('button', { name: 'Open Synthetic freelancer' }));
  expect(screen.getByLabelText('Profile type')).toHaveValue('FREELANCER');
});
test('duplicate skill names are rejected locally without losing form input', async () => {
  const { user, writes } = setup();
  await create(user);
  for (let i = 1; i <= 2; i++) {
    await user.click(screen.getByRole('button', { name: 'Add skill' }));
    await user.type(screen.getByLabelText(`Skill ${i} name`), i === 1 ? 'Python' : 'PYTHON');
  }
  await user.click(save());
  expect(await screen.findByRole('alert')).toHaveTextContent(/unique named skills/);
  expect(writes).toHaveLength(0);
  expect(screen.getByLabelText('Skill 2 name')).toHaveValue('PYTHON');
});
test('revision conflicts keep edits and old revision until the operator explicitly loads current values', async () => {
  const existing = profileRecord();
  let current = existing;
  const { user, writes, records } = setup({
    records: [existing],
    write: () => {
      current = {
        ...existing,
        revision: 2,
        profile: { ...existing.profile, name: 'Other synthetic edit' },
      };
      records.set(current.profile_id, current);
      return Response.json(
        { detail: { code: 'PROFILE_REVISION_CONFLICT', message: 'PRIVATE' } },
        { status: 409 },
      );
    },
  });
  await user.click(await screen.findByRole('button', { name: 'Open Synthetic freelancer' }));
  await user.clear(screen.getByLabelText('Headline or short description'));
  await user.type(screen.getByLabelText('Headline or short description'), 'My retained changes');
  await user.click(save());
  expect(await screen.findByRole('alert')).toHaveTextContent('PROFILE_REVISION_CONFLICT');
  await screen.findByRole('complementary', { name: 'Current saved version' });
  expect(screen.getByLabelText('Headline or short description')).toHaveValue('My retained changes');
  expect(screen.getByText(/Saved revision: 1/)).toBeInTheDocument();
  expect(writes).toHaveLength(1);
  expect(document.body.textContent).not.toContain('PRIVATE');
  await user.click(
    screen.getByRole('button', { name: 'Load current saved version (discard my edits)' }),
  );
  expect(screen.getByLabelText('Profile name')).toHaveValue('Other synthetic edit');
  expect(screen.getByText(/Saved revision: 2/)).toBeInTheDocument();
});
test.each(['network', 'malformed', 'http'] as const)(
  '%s failure preserves input and checks authoritative state without retrying a save',
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
    expect(screen.getByLabelText('Profile name')).toHaveValue('Synthetic freelancer');
    await waitFor(() => expect(save()).toBeEnabled());
    expect(writes).toHaveLength(1);
    expect(screen.queryByText(/Server confirmed/)).not.toBeInTheDocument();
    expect(document.body.textContent).not.toContain('PRIVATE');
    if (kind !== 'http')
      expect(
        screen.getByRole('complementary', { name: 'Current saved version' }),
      ).toHaveTextContent('revision 1');
  },
);
test('a pending save blocks duplicate submissions and overlapping profile changes', async () => {
  let finish!: (response: Response) => void;
  const pending = new Promise<Response>((resolve) => {
    finish = resolve;
  });
  let result: ProfileRecord;
  const { user, writes } = setup({
    write: (_entry, persist) => {
      result = persist();
      return pending;
    },
  });
  await create(user);
  fireEvent.click(save());
  fireEvent.click(save());
  await waitFor(() => expect(writes).toHaveLength(1));
  expect(screen.getByLabelText('Profile name')).toBeDisabled();
  expect(screen.getByRole('button', { name: 'Create profile' })).toBeDisabled();
  await act(async () =>
    finish(
      Response.json({
        schema_version: 'native-profile-save-result-1',
        status: 'CREATED',
        record: result!,
      }),
    ),
  );
  await screen.findByText(/Server confirmed CREATED/);
  expect(writes).toHaveLength(1);
});
test('already-present save confirmation does not imply qualification or matching', async () => {
  const { user } = setup({
    write: (_entry, persist) =>
      Response.json({
        schema_version: 'native-profile-save-result-1',
        status: 'ALREADY_PRESENT',
        record: persist(),
      }),
  });
  await create(user);
  await user.click(save());
  expect(await screen.findByText(/Server confirmed ALREADY_PRESENT/)).toHaveTextContent(
    'Skills remain unverified',
  );
  expect(screen.getByText(/Profiles do not run opportunity matching/)).toBeInTheDocument();
});
test('disabled API and unconfigured integration have explicit safe states', async () => {
  const { writes } = setup({ listError: true });
  expect(await screen.findByRole('alert')).toHaveTextContent('PROFILES_API_DISABLED');
  expect(writes).toHaveLength(0);
});
test('unconfigured profiles page makes no backend requests', () => {
  const { fetcher } = setup({ configured: false });
  expect(screen.getByText(/TenderCite is not configured/)).toBeInTheDocument();
  expect(fetcher).not.toHaveBeenCalled();
});
