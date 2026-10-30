import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, expect, test, vi } from 'vitest';
import { App } from '../App';
import { matchResult, profileMetadata, opportunityMetadata } from '../test/nativeMatching';

afterEach(() => {
  vi.unstubAllGlobals();
  window.history.replaceState({}, '', '/');
});
function setup(
  options: {
    configured?: boolean;
    preview?: (request: Request) => Response | Promise<Response>;
    listError?: string;
    empty?: boolean;
    paginated?: boolean;
  } = {},
) {
  const p = profileMetadata(),
    o = opportunityMetadata();
  const requests: Request[] = [];
  const fetcher = vi.fn(async (request: Request) => {
    const url = new URL(request.url);
    if (request.method === 'POST') {
      requests.push(request);
      return options.preview ? options.preview(request) : Response.json(matchResult());
    }
    if (options.listError)
      return Response.json(
        { detail: { code: options.listError, message: 'PRIVATE' } },
        { status: 404 },
      );
    if (url.pathname.endsWith('/profiles'))
      return Response.json({
        schema_version: 'native-profile-listing-1',
        profiles: options.empty ? [] : [p],
        next_offset: options.paginated && url.searchParams.get('offset') === '0' ? 50 : null,
      });
    if (url.pathname.endsWith('/opportunities'))
      return Response.json({
        schema_version: 'native-opportunity-listing-1',
        opportunities: options.empty ? [] : [o],
        next_offset: options.paginated && url.searchParams.get('offset') === '0' ? 50 : null,
      });
    throw new Error(`Unexpected synthetic request ${url.pathname}`);
  });
  vi.stubGlobal('fetch', fetcher);
  vi.stubGlobal('__TENDERCITE_CONFIGURED__', options.configured ?? true);
  window.history.replaceState({}, '', '/matching');
  render(<App />);
  return { user: userEvent.setup(), fetcher, requests, p, o };
}
async function select(user: ReturnType<typeof userEvent.setup>) {
  await screen.findByRole('option', { name: /Synthetic freelancer/ });
  await user.selectOptions(
    screen.getByRole('combobox', { name: 'Profile' }),
    profileMetadata().profile_id,
  );
  await user.selectOptions(
    screen.getByRole('combobox', { name: 'Opportunity' }),
    opportunityMetadata().opportunity_id,
  );
}
const compare = () => screen.getByRole('button', { name: 'Compare Skills' });
const results = () => screen.queryByRole('region', { name: 'Skill comparison results' });

test('explicit comparison shows original/normalized lists, counts and evidence boundaries', async () => {
  const { user, requests } = setup();
  expect(screen.getByRole('link', { name: 'Opportunity Matching' })).toHaveAttribute(
    'href',
    '/matching',
  );
  expect(
    within(screen.getByRole('navigation', { name: 'Primary navigation' })).getByRole('link', {
      name: 'Capability Matching',
    }),
  ).toHaveAttribute('href', '/tenders/capability-report');
  await select(user);
  expect(requests).toHaveLength(0);
  await user.click(compare());
  const region = await screen.findByRole('region', { name: 'Skill comparison results' });
  expect(
    within(region).getByText('1 of 2 requested skill labels are recorded in this profile.'),
  ).toBeVisible();
  expect(within(region).getByText('MATCHED')).toBeVisible();
  expect(within(region).getByText('NOT_RECORDED')).toBeVisible();
  expect(within(region).getAllByText('python').length).toBeGreaterThan(0);
  expect(within(region).getByText('FastAPI')).toBeVisible();
  expect(
    screen.getByText(/They do not verify experience, qualifications, availability or eligibility/),
  ).toBeVisible();
  expect(localStorage.length).toBe(0);
  expect(sessionStorage.length).toBe(0);
  expect(requests).toHaveLength(1);
});

test.each(['Profile', 'Opportunity'])(
  'changing %s clears comparison without running again',
  async (name) => {
    const { user, requests } = setup();
    await select(user);
    await user.click(compare());
    await screen.findByRole('region', { name: 'Skill comparison results' });
    await user.selectOptions(screen.getByRole('combobox', { name }), '');
    expect(results()).not.toBeInTheDocument();
    expect(requests).toHaveLength(1);
  },
);

test('changing selection aborts old response and prevents overlapping comparison', async () => {
  let complete!: (value: Response) => void;
  const { user, requests } = setup({
    preview: () =>
      new Promise((resolve) => {
        complete = resolve;
      }),
  });
  await select(user);
  fireEvent.click(compare());
  fireEvent.click(compare());
  await waitFor(() => expect(requests).toHaveLength(1));
  expect(compare()).toBeDisabled();
  await user.selectOptions(screen.getByRole('combobox', { name: 'Profile' }), '');
  expect(requests[0]!.signal.aborted).toBe(true);
  await act(async () => complete(Response.json(matchResult())));
  expect(results()).not.toBeInTheDocument();
});

test('unmount discards private result and cancels running request', async () => {
  const { user, requests } = setup({ preview: async () => new Promise(() => {}) });
  await select(user);
  await user.click(compare());
  await user.click(screen.getByRole('link', { name: 'Profiles' }));
  expect(requests[0]!.signal.aborted).toBe(true);
  expect(results()).not.toBeInTheDocument();
});

test.each(['PROFILE_REVISION_CONFLICT', 'OPPORTUNITY_REVISION_CONFLICT'])(
  'stale %s requires explicit refresh and new selection',
  async (code) => {
    let succeed = false;
    const { user, requests, p, o } = setup({
      preview: () => {
        if (!succeed) return Response.json({ detail: { code } }, { status: 409 });
        const result = matchResult();
        result.profile.revision = p.revision;
        result.opportunity.revision = o.revision;
        return Response.json(result);
      },
    });
    await select(user);
    await user.click(compare());
    expect(await screen.findByRole('alert')).toHaveTextContent(code);
    expect(compare()).toBeDisabled();
    expect(requests).toHaveLength(1);
    p.revision = 2;
    o.revision = 2;
    succeed = true;
    await user.click(screen.getByRole('button', { name: 'Refresh saved records' }));
    expect(compare()).toBeDisabled();
    await select(user);
    await user.click(compare());
    await screen.findByRole('region', { name: 'Skill comparison results' });
    expect(await requests[1]!.json()).toMatchObject({
      expected_profile_revision: 2,
      expected_opportunity_revision: 2,
    });
  },
);

test.each(['disabled', 'http', 'network', 'invalid'])(
  'safe %s comparison failure never retries or exposes private bodies',
  async (kind) => {
    const { user, requests } = setup({
      preview: () => {
        if (kind === 'network') throw new Error('PRIVATE');
        if (kind === 'invalid') return Response.json({ private: 'PRIVATE' });
        return Response.json(
          {
            detail: { code: kind === 'disabled' ? 'NATIVE_MATCHING_API_DISABLED' : 'PRIVATE' },
            private: 'PRIVATE',
          },
          { status: 503 },
        );
      },
    });
    await select(user);
    await user.click(compare());
    expect(await screen.findByRole('alert')).not.toHaveTextContent('PRIVATE');
    expect(compare()).toBeDisabled();
    expect(requests).toHaveLength(1);
    expect(results()).not.toBeInTheDocument();
  },
);

test('unconfigured proxy sends no requests', () => {
  const { fetcher } = setup({ configured: false });
  expect(screen.getByRole('alert')).toHaveTextContent('not configured');
  expect(fetcher).not.toHaveBeenCalled();
});

test('disabled listing shows safe unavailable state and no preview', async () => {
  const { requests } = setup({ listError: 'PROFILES_API_DISABLED' });
  const alerts = await screen.findAllByRole('alert');
  expect(alerts[0]).not.toHaveTextContent('PRIVATE');
  expect(compare()).toBeDisabled();
  expect(requests).toHaveLength(0);
});

test('empty lists guide creation without automatic preview', async () => {
  const { requests } = setup({ empty: true });
  expect(await screen.findByText(/No saved profiles on this page/)).toBeVisible();
  expect(screen.getByText(/No saved opportunities on this page/)).toBeVisible();
  expect(compare()).toBeDisabled();
  expect(requests).toHaveLength(0);
});

test('pagination clears selection and results; supports records beyond first 50', async () => {
  const { user, fetcher, requests } = setup({ paginated: true });
  await select(user);
  await user.click(compare());
  await screen.findByRole('region', { name: 'Skill comparison results' });
  await user.click(screen.getByRole('button', { name: 'Next profiles' }));
  expect(results()).not.toBeInTheDocument();
  expect(compare()).toBeDisabled();
  await waitFor(() =>
    expect(fetcher.mock.calls.some(([r]) => r.url.endsWith('profiles?limit=50&offset=50'))).toBe(
      true,
    ),
  );
  expect(requests).toHaveLength(1);
});

test.each(['empty-profile', 'NO_REQUIREMENTS'])(
  'shows explicit %s result without a perfect-match claim',
  async (kind) => {
    const response = matchResult();
    if (kind === 'NO_REQUIREMENTS') {
      response.status = 'NO_REQUIREMENTS';
      response.opportunity.required_skills = [];
      response.counts = { total_opportunity_skills: 0, matched_skills: 0, not_recorded_skills: 0 };
    } else {
      response.profile.skills = [];
      response.opportunity.required_skills.forEach((s) => {
        s.status = 'NOT_RECORDED';
        s.matched_profile_skill_ids = [];
      });
      response.counts.matched_skills = 0;
      response.counts.not_recorded_skills = 2;
    }
    const { user } = setup({ preview: async () => Response.json(response) });
    await select(user);
    await user.click(compare());
    const region = await screen.findByRole('region', { name: 'Skill comparison results' });
    expect(region).toHaveTextContent(
      kind === 'NO_REQUIREMENTS' ? 'NO_REQUIREMENTS' : 'No declared profile skills.',
    );
    expect(region).not.toHaveTextContent('perfect match');
  },
);
