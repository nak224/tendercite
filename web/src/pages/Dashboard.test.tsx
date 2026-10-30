import { act, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, expect, test, vi } from 'vitest';
import { App } from '../App';
import { profileMetadata, opportunityMetadata } from '../test/nativeMatching';
import { pipelineEntry } from '../test/pipeline';
import { localCalendarDate } from '../utils/actionDate';
import type { PipelineEntry } from '../api/pipeline';

afterEach(() => {
  vi.unstubAllGlobals();
  window.history.replaceState({}, '', '/');
});
function setup(
  options: {
    empty?: boolean;
    configured?: boolean;
    disabled?: string;
    pendingProfiles?: Promise<Response>;
    profileCount?: number;
    more?: boolean;
    entries?: PipelineEntry[];
  } = {},
) {
  const profiles = Array.from(
    { length: options.empty ? 0 : (options.profileCount ?? 1) },
    (_, i) => ({
      ...profileMetadata(),
      profile_id: `20000000-0000-0000-0000-${String(i + 1).padStart(12, '0')}`,
      name: i ? `Synthetic profile ${i + 1}` : 'Synthetic freelancer',
      skills: ['PRIVATE'],
    }),
  );
  const opportunities = options.empty ? [] : [opportunityMetadata()];
  const entries = options.empty ? [] : (options.entries ?? [pipelineEntry()]);
  const fetcher = vi.fn(async (request: Request) => {
    expect(new URL(request.url).origin).toBe(window.location.origin);
    expect(request.method).toBe('GET');
    const url = new URL(request.url),
      resource = url.pathname.split('/').at(-1);
    expect(['profiles', 'opportunities', 'pipeline']).toContain(resource);
    expect(url.searchParams.get('limit')).toBe('50');
    expect(url.searchParams.get('offset')).toBe('0');
    if (options.disabled === resource)
      return Response.json(
        {
          detail: {
            code: resource === 'profiles' ? 'PROFILES_API_DISABLED' : 'PIPELINE_API_DISABLED',
            message: 'PRIVATE',
          },
        },
        { status: 404 },
      );
    if (resource === 'profiles')
      return (
        options.pendingProfiles ??
        Response.json({
          schema_version: 'native-profile-listing-1',
          profiles,
          next_offset: options.more ? 50 : null,
        })
      );
    if (resource === 'opportunities')
      return Response.json({
        schema_version: 'native-opportunity-listing-1',
        opportunities,
        next_offset: null,
      });
    return Response.json({
      schema_version: 'native-pipeline-listing-1',
      entries,
      next_offset: null,
    });
  });
  vi.stubGlobal('__TENDERCITE_CONFIGURED__', options.configured ?? true);
  vi.stubGlobal('fetch', fetcher);
  window.history.replaceState({}, '', '/dashboard');
  render(<App />);
  return { user: userEvent.setup(), fetcher, entries };
}
const region = (name: string) => screen.getByRole('region', { name });
test('quick actions use existing pages without duplicate forms or automatic operations', async () => {
  const { fetcher } = setup({ empty: true });
  await screen.findByText(/No saved profiles in this preview/);
  const actions = within(screen.getByRole('group', { name: 'Quick actions' }));
  for (const [name, path] of [
    ['Create Profile', '/profiles'],
    ['Add Opportunity', '/opportunities'],
    ['Compare Skills', '/matching'],
    ['View Pipeline', '/pipeline'],
  ])
    expect(actions.getByRole('link', { name })).toHaveAttribute('href', path);
  expect(within(region('Profiles')).getByText(/No saved profiles/)).toBeVisible();
  expect(within(region('Opportunities')).getByText(/No saved opportunities/)).toBeVisible();
  expect(within(region('Pipeline')).getByText(/No tracked opportunities/)).toBeVisible();
  expect(screen.queryByRole('textbox')).not.toBeInTheDocument();
  expect(fetcher).toHaveBeenCalledTimes(3);
  expect(localStorage.length + sessionStorage.length).toBe(0);
});
test('compact metadata previews distinguish manual records from tender analysis', async () => {
  const entry = pipelineEntry();
  entry.record.values.notes = 'PRIVATE';
  entry.record.values.next_action = 'Prepare synthetic proposal';
  const { fetcher } = setup({ entries: [entry] });
  await screen.findByText('Synthetic freelancer');
  expect(region('Profiles')).toHaveTextContent('FREELANCER');
  expect(region('Opportunities')).toHaveTextContent('Synthetic Python project');
  expect(region('Opportunities')).toHaveTextContent('Synthetic client');
  expect(region('Opportunities')).toHaveTextContent('2027-03-01');
  expect(region('Opportunities')).toHaveTextContent('not verified tender analyses');
  expect(region('Pipeline')).toHaveTextContent('Prepare synthetic proposal');
  expect(region('Pipeline')).toHaveTextContent('Priority MEDIUM');
  expect(region('Tender Analysis')).toHaveTextContent(
    'has not automatically undergone document analysis',
  );
  expect(screen.queryByText(/PRIVATE/)).not.toBeInTheDocument();
  expect(fetcher).toHaveBeenCalledTimes(3);
});
test('contextual links carry saved UUIDs only, and never trigger operations', async () => {
  const { fetcher } = setup();
  await screen.findByText('Synthetic freelancer');
  expect(
    within(region('Opportunities')).getByRole('link', { name: 'Compare this opportunity' }),
  ).toHaveAttribute('href', `/matching?opportunity_id=${opportunityMetadata().opportunity_id}`);
  expect(
    within(region('Opportunities')).getByRole('link', { name: 'Track this opportunity' }),
  ).toHaveAttribute('href', `/pipeline?opportunity_id=${opportunityMetadata().opportunity_id}`);
  expect(
    within(region('Pipeline')).getByRole('link', { name: 'Open existing pipeline entry' }),
  ).toHaveAttribute('href', `/pipeline?pipeline_id=${pipelineEntry().record.pipeline_id}`);
  expect(fetcher.mock.calls.every(([r]) => r.method === 'GET')).toBe(true);
});
test('bounded preview discloses partial pages and does not fetch totals or follow pagination', async () => {
  const { fetcher } = setup({ profileCount: 50, more: true });
  await screen.findByText('Synthetic freelancer');
  expect(region('Profiles')).toHaveTextContent(
    'Showing 4 of 50 loaded records from the first page',
  );
  expect(region('Profiles')).toHaveTextContent('More saved records are available');
  expect(screen.queryByText('Synthetic profile 5')).not.toBeInTheDocument();
  expect(within(region('Profiles')).getAllByRole('heading', { level: 3 })).toHaveLength(4);
  expect(fetcher).toHaveBeenCalledTimes(3);
  expect(fetcher.mock.calls.every(([r]) => new URL(r.url).searchParams.get('offset') === '0')).toBe(
    true,
  );
});
test.each(['profiles', 'opportunities', 'pipeline'])(
  'disabled %s preview does not break independent workflows',
  async (resource) => {
    setup({ disabled: resource });
    const alert = await screen.findByRole('alert');
    expect(alert).not.toHaveTextContent('PRIVATE');
    expect(alert).toHaveTextContent('preview unavailable');
    expect(
      within(region('Tender Analysis')).getByRole('link', { name: 'Documents & findings' }),
    ).toHaveAttribute('href', '/tenders');
    if (resource !== 'profiles')
      expect(await screen.findByText('Synthetic freelancer')).toBeVisible();
    if (resource !== 'pipeline') expect(region('Pipeline')).toHaveTextContent('Priority MEDIUM');
    expect(
      within(region('Opportunities')).getByRole('link', { name: 'Manage opportunities' }),
    ).toBeVisible();
  },
);
test('loading is independent, and completion does not refetch unrelated previews', async () => {
  let complete!: (value: Response) => void;
  const { fetcher } = setup({
    pendingProfiles: new Promise((resolve) => {
      complete = resolve;
    }),
  });
  expect(await screen.findByText('Priority MEDIUM')).toBeVisible();
  expect(region('Profiles')).toHaveTextContent('Loading Profiles preview');
  await act(async () =>
    complete(
      Response.json({
        schema_version: 'native-profile-listing-1',
        profiles: [],
        next_offset: null,
      }),
    ),
  );
  await screen.findByText(/No saved profiles in this preview/);
  expect(fetcher).toHaveBeenCalledTimes(3);
});
test('explicit refresh reads current persisted actions, with no polling or writes', async () => {
  const { user, fetcher, entries } = setup();
  await screen.findByText('Synthetic freelancer');
  entries[0]!.record.values.next_action = 'Updated persisted synthetic action';
  await user.click(screen.getByRole('button', { name: 'Refresh previews' }));
  await screen.findByText('Next action: Updated persisted synthetic action');
  await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(6));
  expect(fetcher.mock.calls.every(([r]) => r.method === 'GET')).toBe(true);
});
test.each([-1, 0, 1])(
  'action date %+i days uses date-only local calendar labels',
  async (delta) => {
    const entry = pipelineEntry(),
      date = new Date();
    date.setDate(date.getDate() + delta);
    entry.record.values.next_action_due_date = localCalendarDate(date);
    setup({ entries: [entry] });
    const expected =
      delta < 0
        ? 'Overdue action date'
        : delta === 0
          ? 'Action date today'
          : 'Upcoming action date';
    expect(await screen.findByText(expected)).toBeVisible();
    expect(region('Pipeline')).toHaveTextContent(entry.record.values.next_action_due_date);
  },
);
test('closed pipeline outcome is a manual declaration, not overdue unfinished work', async () => {
  const entry = pipelineEntry();
  entry.record.values.stage = 'ARCHIVED';
  entry.record.values.next_action_due_date = '2000-01-01';
  setup({ entries: [entry] });
  expect(await screen.findByText('Recorded action date')).toBeVisible();
  expect(screen.queryByText('Overdue action date')).not.toBeInTheDocument();
});
test('unconfigured dashboard makes no requests and retains workflow entry points', () => {
  const { fetcher } = setup({ configured: false });
  expect(screen.getByText('Local backend not configured')).toBeVisible();
  expect(
    within(region('Tender Analysis')).getByRole('link', { name: 'Documents & findings' }),
  ).toBeVisible();
  expect(fetcher).not.toHaveBeenCalled();
});
