import { test, expect } from '@playwright/test';

test('dashboard → profile → opportunity → compare → track → next action → dashboard readback', async ({
  page,
  request,
  baseURL,
}) => {
  test.skip(
    process.env.TENDERCITE_DASHBOARD_SMOKE !== '1',
    'Requires its own empty disposable synthetic database',
  );
  const api = `${baseURL}/tendercite-api/api/v1`;
  for (const resource of ['profiles', 'opportunities', 'pipeline']) {
    const response = await request.get(`${api}/${resource}`);
    expect(response.status()).toBe(200);
    expect((await response.json())[resource === 'pipeline' ? 'entries' : resource]).toEqual([]);
  }
  const configuration = await (await request.get(`${api}/configuration`)).json();
  expect(configuration.retrieval_enabled).toBe(false);
  expect(configuration.llm_configured).toBe(false);
  const writes: string[] = [],
    comparisons: string[] = [],
    errors: string[] = [];
  page.on('pageerror', (error) => errors.push(error.message));
  page.on('request', (sent) => {
    expect(new URL(sent.url()).origin).toBe(baseURL);
    const path = new URL(sent.url()).pathname;
    if (!path.startsWith('/tendercite-api/')) return;
    expect(path).toMatch(
      /^\/tendercite-api\/api\/v1\/(?:profiles|opportunities|pipeline|native-matches\/preview)(?:\/[a-f0-9-]+)?$/,
    );
    if (sent.method() === 'GET') return;
    if (path.endsWith('/native-matches/preview')) comparisons.push(path);
    else writes.push(`${sent.method()} ${path}`);
  });
  const nav = page.getByRole('navigation', { name: 'Primary navigation' });
  await page.goto('/');
  await expect(page).toHaveURL(`${baseURL}/dashboard`);
  await expect(page.getByText(/No saved profiles in this preview/)).toBeVisible();
  await page
    .getByRole('group', { name: 'Quick actions' })
    .getByRole('link', { name: 'Create Profile', exact: true })
    .click();
  await page.getByRole('button', { name: 'Create profile', exact: true }).click();
  await page.getByLabel('Profile name', { exact: true }).fill('Integrated synthetic freelancer');
  for (const [i, skill] of ['Python', 'FastAPI'].entries()) {
    await page.getByRole('button', { name: 'Add skill', exact: true }).click();
    await page.getByLabel(`Skill ${i + 1} name`, { exact: true }).fill(skill);
  }
  await page.getByRole('button', { name: 'Save profile', exact: true }).click();
  await expect(page.getByText(/Server confirmed CREATED/)).toBeVisible();
  await nav.getByRole('link', { name: 'Dashboard', exact: true }).click();
  await expect(page.getByRole('region', { name: 'Profiles' })).toContainText(
    'Integrated synthetic freelancer',
  );
  await page
    .getByRole('group', { name: 'Quick actions' })
    .getByRole('link', { name: 'Add Opportunity', exact: true })
    .click();
  await page.getByRole('button', { name: 'Create Opportunity', exact: true }).click();
  await page.getByLabel('Opportunity title', { exact: true }).fill('Integrated synthetic project');
  await page.getByLabel('Organization or client (optional)').fill('Synthetic client');
  await page.getByLabel('Application/submission deadline (date only, optional)').fill('2027-03-01');
  for (const [i, skill] of ['python', 'TypeScript'].entries()) {
    await page.getByRole('button', { name: 'Add required skill', exact: true }).click();
    await page.getByLabel(`Skill ${i + 1} name`, { exact: true }).fill(skill);
  }
  const createdOpportunity = page.waitForResponse(
    (r) => r.url().endsWith('/opportunities') && r.request().method() === 'POST',
  );
  await page.getByRole('button', { name: 'Save Opportunity', exact: true }).click();
  const opportunity = (await (await createdOpportunity).json()).record;
  await expect(page.getByText(/Server confirmed CREATED/)).toBeVisible();
  await nav.getByRole('link', { name: 'Dashboard', exact: true }).click();
  const opportunitySection = page.getByRole('region', { name: 'Opportunities' });
  await expect(opportunitySection).toContainText('Integrated synthetic project');
  await opportunitySection
    .getByRole('link', { name: 'Compare this opportunity', exact: true })
    .click();
  await expect(page.getByRole('combobox', { name: 'Opportunity', exact: true })).toHaveValue(
    opportunity.opportunity_id,
  );
  await page
    .getByRole('combobox', { name: 'Profile', exact: true })
    .selectOption({ label: 'Integrated synthetic freelancer · FREELANCER · revision 1' });
  expect(comparisons).toHaveLength(0);
  expect(writes).toHaveLength(2);
  await page.getByRole('button', { name: 'Compare Skills', exact: true }).click();
  await expect(
    page.getByText('1 of 2 requested skill labels are recorded in this profile.'),
  ).toBeVisible();
  await page.getByRole('link', { name: 'Track selected opportunity', exact: true }).click();
  await expect(
    page.getByRole('combobox', { name: 'Existing opportunity', exact: true }),
  ).toHaveValue(opportunity.opportunity_id);
  await expect(page.getByRole('button', { name: 'Add to Pipeline', exact: true })).toBeEnabled();
  expect(writes).toHaveLength(2);
  const tracked = page.waitForResponse(
    (r) => r.url().endsWith('/pipeline') && r.request().method() === 'POST',
  );
  await page.getByRole('button', { name: 'Add to Pipeline', exact: true }).click();
  const entry = (await (await tracked).json()).entry;
  await expect(page.getByText(/Server confirmed CREATED/)).toBeVisible();
  await page.getByRole('combobox', { name: 'Stage', exact: true }).selectOption('PREPARING');
  await page.getByRole('combobox', { name: 'Priority', exact: true }).selectOption('HIGH');
  await page
    .getByLabel('Next action (optional, maximum 1000 characters)')
    .fill('Prepare synthetic proposal locally');
  const due = await page.evaluate(() => {
    const date = new Date();
    date.setDate(date.getDate() + 1);
    return [
      date.getFullYear(),
      String(date.getMonth() + 1).padStart(2, '0'),
      String(date.getDate()).padStart(2, '0'),
    ].join('-');
  });
  await page.getByLabel('Next-action due date (optional, date only)').fill(due);
  await page.getByRole('button', { name: 'Save Changes', exact: true }).click();
  await expect(page.getByText(/Server confirmed UPDATED at revision 2/)).toBeVisible();
  await expect(page.getByRole('button', { name: 'Save Changes', exact: true })).toBeEnabled();
  await nav.getByRole('link', { name: 'Dashboard', exact: true }).click();
  await page.reload();
  const pipeline = page.getByRole('region', { name: 'Pipeline' });
  await expect(pipeline).toContainText('Integrated synthetic project');
  await expect(pipeline).toContainText('PREPARING');
  await expect(pipeline).toContainText('Priority HIGH');
  await expect(pipeline).toContainText('Prepare synthetic proposal locally');
  await expect(pipeline).toContainText(due);
  await expect(pipeline.getByText('Upcoming action date', { exact: true })).toBeVisible();
  await pipeline.getByRole('link', { name: 'Open existing pipeline entry', exact: true }).click();
  await expect(page.getByRole('region', { name: 'Pipeline editor' })).toContainText('Revision 2');
  await expect(page.getByLabel('Next action (optional, maximum 1000 characters)')).toHaveValue(
    'Prepare synthetic proposal locally',
  );
  await nav.getByRole('link', { name: 'Dashboard', exact: true }).click();
  await expect(page.getByRole('region', { name: 'Profiles' })).toContainText(
    'Integrated synthetic freelancer',
  );
  const stored = await (await request.get(`${api}/pipeline/${entry.record.pipeline_id}`)).json();
  expect(stored.record.revision).toBe(2);
  expect(stored.record.values.next_action_due_date).toBe(due);
  expect(writes).toHaveLength(4);
  expect(comparisons).toHaveLength(1);
  expect(errors).toEqual([]);
  expect(await page.evaluate(() => localStorage.length + sessionStorage.length)).toBe(0);
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.screenshot({
    path: test.info().outputPath('dashboard-synthetic-desktop.png'),
    fullPage: true,
  });
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(page.getByRole('heading', { name: 'TenderCite Dashboard' })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(
    true,
  );
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.screenshot({
    path: test.info().outputPath('dashboard-synthetic-mobile.png'),
    fullPage: true,
  });
});
