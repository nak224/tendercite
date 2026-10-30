import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, expect, test, vi } from 'vitest';
import { App } from '../App';
import { opportunityRecord } from '../test/opportunities';
import { profileMetadata } from '../test/nativeMatching';
import { pipelineEntry, pipelineId } from '../test/pipeline';

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  window.history.replaceState({}, '', '/');
});
function setup(
  path: string,
  options: { wrongIdentity?: boolean; missing?: boolean; disabledOpportunities?: boolean } = {},
) {
  const source = opportunityRecord(),
    entry = pipelineEntry();
  const fetcher = vi.fn(async (request: Request) => {
    expect(request.method).toBe('GET');
    expect(new URL(request.url).origin).toBe(window.location.origin);
    const url = new URL(request.url);
    if (url.pathname.endsWith('/profiles'))
      return Response.json({
        schema_version: 'native-profile-listing-1',
        profiles: [profileMetadata()],
        next_offset: null,
      });
    if (url.pathname.endsWith('/opportunities')) {
      if (options.disabledOpportunities)
        return Response.json({ detail: { code: 'OPPORTUNITIES_API_DISABLED' } }, { status: 404 });
      // Linked source lies outside the bounded first page.
      return Response.json({
        schema_version: 'native-opportunity-listing-1',
        opportunities: [],
        next_offset: null,
      });
    }
    if (url.pathname.includes('/opportunities/')) {
      if (options.missing)
        return Response.json(
          { detail: { code: 'OPPORTUNITY_NOT_FOUND', message: 'PRIVATE' } },
          { status: 404 },
        );
      return Response.json(
        options.wrongIdentity ? { ...source, opportunity_id: pipelineId } : source,
      );
    }
    if (url.pathname.endsWith('/pipeline'))
      return Response.json({
        schema_version: 'native-pipeline-listing-1',
        entries: [],
        next_offset: null,
      });
    if (url.pathname.includes('/pipeline/'))
      return Response.json(
        options.wrongIdentity
          ? { ...entry, record: { ...entry.record, pipeline_id: source.opportunity_id } }
          : entry,
      );
    throw new Error('Unexpected synthetic URL');
  });
  vi.stubGlobal('fetch', fetcher);
  vi.stubGlobal('__TENDERCITE_CONFIGURED__', true);
  window.history.replaceState({}, '', path);
  render(<App />);
  return { fetcher, user: userEvent.setup(), source };
}
test('matching context loads and selects the saved opportunity, including beyond the first page', async () => {
  const source = opportunityRecord();
  const { fetcher, user } = setup(`/matching?opportunity_id=${source.opportunity_id}`);
  const select = await screen.findByRole('combobox', { name: 'Opportunity' });
  expect(select).toHaveValue(source.opportunity_id);
  expect(screen.getByRole('option', { name: /Synthetic Python project/ })).toHaveTextContent(
    'revision 1',
  );
  await user.selectOptions(
    screen.getByRole('combobox', { name: 'Profile' }),
    profileMetadata().profile_id,
  );
  expect(screen.getByRole('button', { name: 'Compare Skills' })).toBeEnabled();
  expect(fetcher.mock.calls.filter(([r]) => r.url.includes('/opportunities/'))).toHaveLength(1);
  expect(fetcher.mock.calls.every(([r]) => r.method === 'GET')).toBe(true);
  expect(screen.getByRole('link', { name: 'Track selected opportunity' })).toHaveAttribute(
    'href',
    `/pipeline?opportunity_id=${source.opportunity_id}`,
  );
});
test('tracking context selects a source from the API and still requires explicit Add to Pipeline', async () => {
  const source = opportunityRecord();
  const { fetcher } = setup(`/pipeline?opportunity_id=${source.opportunity_id}`);
  expect(await screen.findByRole('combobox', { name: 'Existing opportunity' })).toHaveValue(
    source.opportunity_id,
  );
  await waitFor(() =>
    expect(screen.getByRole('button', { name: 'Add to Pipeline' })).toBeEnabled(),
  );
  expect(screen.queryByRole('region', { name: 'Pipeline editor' })).not.toBeInTheDocument();
  expect(fetcher.mock.calls.every(([r]) => r.method === 'GET')).toBe(true);
});
test('pipeline identity link opens current authoritative revision without requiring opportunity CRUD flag', async () => {
  const { fetcher } = setup(`/pipeline?pipeline_id=${pipelineId}`, { disabledOpportunities: true });
  const editor = await screen.findByRole('region', { name: 'Pipeline editor' });
  expect(editor).toHaveTextContent(pipelineId);
  expect(editor).toHaveTextContent('Revision 1');
  expect(within(editor).getByRole('combobox', { name: 'Stage' })).toHaveValue('SAVED');
  expect(fetcher.mock.calls.every(([r]) => r.method === 'GET')).toBe(true);
});
test.each([
  '/matching?opportunity_id=PRIVATE',
  '/matching?opportunity_id=https://example.invalid',
  `/matching?opportunity_id=${opportunityRecord().opportunity_id}&opportunity_id=${opportunityRecord().opportunity_id}`,
  `/matching?opportunity_id=${opportunityRecord().opportunity_id}&title=PRIVATE`,
  `/pipeline?opportunity_id=${opportunityRecord().opportunity_id}&pipeline_id=${pipelineId}`,
  '/pipeline?pipeline_id=PRIVATE',
  '/matching?pipeline_id=40000000-0000-0000-0000-000000000001',
])('invalid or ambiguous contextual link never queries or mutates: %s', async (path) => {
  const { fetcher } = setup(path);
  expect(screen.getByRole('alert')).toHaveTextContent('Invalid saved-record link');
  expect(screen.getByRole('alert')).not.toHaveTextContent('PRIVATE');
  expect(fetcher).not.toHaveBeenCalled();
  expect(screen.getByRole('link', { name: 'Choose records manually' })).toBeVisible();
});
test.each(['missing', 'wrongIdentity'] as const)(
  'unavailable or mismatched opportunity context fails safely: %s',
  async (kind) => {
    const { fetcher } = setup(`/matching?opportunity_id=${opportunityRecord().opportunity_id}`, {
      [kind]: true,
    });
    expect(await screen.findByRole('alert')).not.toHaveTextContent('PRIVATE');
    expect(screen.queryByRole('button', { name: 'Compare Skills' })).not.toBeInTheDocument();
    expect(fetcher).toHaveBeenCalledTimes(1);
  },
);
test('pipeline identity mismatch cannot load a different entry', async () => {
  const { fetcher } = setup(`/pipeline?pipeline_id=${pipelineId}`, { wrongIdentity: true });
  expect(await screen.findByRole('alert')).toHaveTextContent('Invalid or mismatched');
  expect(screen.queryByRole('region', { name: 'Pipeline editor' })).not.toBeInTheDocument();
  expect(fetcher).toHaveBeenCalledTimes(1);
});
test('contextual navigation out of a dirty pipeline editor preserves the existing discard guard', async () => {
  const { user } = setup(`/pipeline?pipeline_id=${pipelineId}`);
  await screen.findByRole('region', { name: 'Pipeline editor' });
  await user.type(
    screen.getByLabelText('Notes (optional, maximum 4000 characters)'),
    'Unsaved synthetic text',
  );
  await user.click(
    within(screen.getByRole('navigation', { name: 'Primary navigation' })).getByRole('link', {
      name: 'Dashboard',
    }),
  );
  expect(
    await screen.findByRole('alertdialog', { name: 'Unsaved pipeline changes' }),
  ).toBeVisible();
  await user.click(screen.getByRole('button', { name: 'Keep editing' }));
  expect(screen.getByLabelText('Notes (optional, maximum 4000 characters)')).toHaveValue(
    'Unsaved synthetic text',
  );
});
