import { test, expect } from '@playwright/test';

test('manual opportunity → explicit tracking → stage/action → save → reload → readback', async ({
  page,
  request,
  baseURL,
}) => {
  test.skip(
    process.env.TENDERCITE_PIPELINE_SMOKE !== '1',
    'Requires its own fresh disposable synthetic database',
  );
  const api = `${baseURL}/tendercite-api/api/v1`;
  for (const resource of ['opportunities', 'pipeline']) {
    const initial = await request.get(`${api}/${resource}`);
    expect(initial.status()).toBe(200);
    expect((await initial.json())[resource === 'pipeline' ? 'entries' : resource]).toEqual([]);
  }
  const writes: string[] = [],
    errors: string[] = [];
  page.on('pageerror', (error) => errors.push(error.message));
  page.on('request', (sent) => {
    expect(new URL(sent.url()).origin).toBe(baseURL);
    const path = new URL(sent.url()).pathname;
    if (path.startsWith('/tendercite-api/')) {
      expect(path).toMatch(
        /^\/tendercite-api\/api\/v1\/(?:opportunities|pipeline)(?:\/[a-f0-9-]+)?$/,
      );
      if (sent.method() !== 'GET') writes.push(`${sent.method()} ${path}`);
    }
  });
  await page.goto('/opportunities');
  await expect(page.getByText('No saved opportunities yet.')).toBeVisible();
  await page.getByRole('button', { name: 'Create Opportunity', exact: true }).click();
  await page.getByLabel('Opportunity title', { exact: true }).fill('Synthetic pipeline project');
  await page.getByLabel('Organization or client (optional)').fill('Synthetic client');
  await page.getByLabel('Application/submission deadline (date only, optional)').fill('2027-03-01');
  const savedOpportunity = page.waitForResponse(
    (r) => r.url().endsWith('/opportunities') && r.request().method() === 'POST',
  );
  await page.getByRole('button', { name: 'Save Opportunity', exact: true }).click();
  const opportunity = (await (await savedOpportunity).json()).record;
  await expect(page.getByText(/Server confirmed CREATED/)).toBeVisible();
  expect((await (await request.get(`${api}/pipeline`)).json()).entries).toEqual([]);
  await page
    .getByRole('navigation', { name: 'Primary navigation' })
    .getByRole('link', { name: 'Pipeline', exact: true })
    .click();
  await page
    .getByRole('combobox', { name: 'Existing opportunity', exact: true })
    .selectOption(opportunity.opportunity_id);
  await expect(page.getByRole('button', { name: 'Add to Pipeline', exact: true })).toBeEnabled();
  expect(writes).toHaveLength(1);
  const created = page.waitForResponse(
    (r) => r.url().endsWith('/pipeline') && r.request().method() === 'POST',
  );
  await page.getByRole('button', { name: 'Add to Pipeline', exact: true }).click();
  const entry = (await (await created).json()).entry;
  await expect(page.getByText(/Server confirmed CREATED/)).toBeVisible();
  expect(entry.record.values.stage).toBe('SAVED');
  await page.getByRole('combobox', { name: 'Stage', exact: true }).selectOption('SUBMITTED');
  await page.getByRole('combobox', { name: 'Priority', exact: true }).selectOption('HIGH');
  await page
    .getByLabel('Notes (optional, maximum 4000 characters)')
    .fill('Synthetic declaration, not an external submission.');
  await page
    .getByLabel('Next action (optional, maximum 1000 characters)')
    .fill('Check the synthetic external portal manually');
  await page.getByLabel('Next-action due date (optional, date only)').fill('2027-02-15');
  expect(writes).toHaveLength(2);
  await page.goBack();
  await expect(page.getByRole('alertdialog', { name: 'Unsaved pipeline changes' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Keep editing', exact: true })).toBeFocused();
  await page.getByRole('button', { name: 'Keep editing', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Pipeline', exact: true })).toBeVisible();
  await expect(page.getByRole('combobox', { name: 'Stage', exact: true })).toHaveValue('SUBMITTED');
  const updated = page.waitForResponse(
    (r) =>
      r.url().endsWith(`/pipeline/${entry.record.pipeline_id}`) && r.request().method() === 'PUT',
  );
  await page.getByRole('button', { name: 'Save Changes', exact: true }).click();
  const response = await updated;
  expect(response.status()).toBe(200);
  expect(response.request().postDataJSON()).toEqual({
    schema_version: 'native-pipeline-update-1',
    expected_revision: 1,
    values: {
      stage: 'SUBMITTED',
      priority: 'HIGH',
      notes: 'Synthetic declaration, not an external submission.',
      next_action: 'Check the synthetic external portal manually',
      next_action_due_date: '2027-02-15',
    },
  });
  await expect(page.getByText(/Server confirmed UPDATED at revision 2/)).toBeVisible();
  await expect(page.getByRole('button', { name: 'Save Changes', exact: true })).toBeEnabled();
  await page.reload();
  await page.getByRole('button', { name: 'Open Synthetic pipeline project', exact: true }).click();
  await expect(page.getByRole('combobox', { name: 'Stage', exact: true })).toHaveValue('SUBMITTED');
  await expect(page.getByRole('combobox', { name: 'Priority', exact: true })).toHaveValue('HIGH');
  await expect(page.getByLabel('Next-action due date (optional, date only)')).toHaveValue(
    '2027-02-15',
  );
  await expect(page.getByText(/Original opportunity:/)).toContainText('2027-03-01');
  await page.getByRole('combobox', { name: 'Filter by stage', exact: true }).selectOption('WON');
  await expect(page.getByText('No tracked opportunities in this view.')).toBeVisible();
  await page
    .getByRole('combobox', { name: 'Filter by stage', exact: true })
    .selectOption('SUBMITTED');
  await expect(
    page.getByRole('button', { name: 'Open Synthetic pipeline project', exact: true }),
  ).toBeVisible();
  await page
    .getByRole('combobox', { name: 'Existing opportunity', exact: true })
    .selectOption(opportunity.opportunity_id);
  await expect(
    page.getByRole('button', { name: 'Open existing pipeline entry', exact: true }),
  ).toBeVisible();
  await expect(page.getByRole('button', { name: 'Add to Pipeline', exact: true })).toBeDisabled();
  await page.getByRole('button', { name: 'Open existing pipeline entry', exact: true }).click();
  const current = await (await request.get(`${api}/pipeline/${entry.record.pipeline_id}`)).json();
  expect(current.record.revision).toBe(2);
  expect(current.record.values.stage).toBe('SUBMITTED');
  const source = await (
    await request.get(`${api}/opportunities/${opportunity.opportunity_id}`)
  ).json();
  expect(source).toEqual(opportunity);
  expect(writes).toHaveLength(3);
  expect(errors).toEqual([]);
  expect(await page.evaluate(() => localStorage.length + sessionStorage.length)).toBe(0);
  await page.screenshot({
    path: test.info().outputPath('native-pipeline-synthetic.png'),
    fullPage: true,
  });
});
