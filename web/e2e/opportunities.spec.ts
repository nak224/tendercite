import { test, expect } from '@playwright/test';

test('manual opportunity → required skills → save → reload → edit → save', async ({
  page,
  request,
  baseURL,
}) => {
  test.skip(
    process.env.TENDERCITE_SYNTHETIC_SMOKE !== '1',
    'Requires fresh disposable synthetic data',
  );
  const initial = await request.get(`${baseURL}/tendercite-api/api/v1/opportunities`);
  expect(initial.status()).toBe(200);
  expect((await initial.json()).opportunities).toEqual([]);
  const writes: string[] = [];
  const errors: string[] = [];
  page.on('pageerror', (error) => errors.push(error.message));
  page.on('request', (sent) => {
    expect(new URL(sent.url()).origin).toBe(baseURL);
    const path = new URL(sent.url()).pathname;
    if (path.startsWith('/tendercite-api/')) {
      expect(path).toMatch(/^\/tendercite-api\/api\/v1\/opportunities(?:\/[a-f0-9-]+)?$/);
      if (sent.method() !== 'GET') writes.push(sent.method());
    }
  });
  await page.goto('/opportunities');
  await expect(page.getByText('No saved opportunities yet.')).toBeVisible();
  await page.getByRole('button', { name: 'Create Opportunity', exact: true }).click();
  await page
    .getByLabel('Opportunity title', { exact: true })
    .fill('Synthetic external Python project');
  await page
    .getByRole('combobox', { name: 'Opportunity type', exact: true })
    .selectOption('FREELANCE_PROJECT');
  await page.getByLabel('Organization or client (optional)').fill('Synthetic organization');
  await page
    .getByLabel('Short description (optional)')
    .fill('Self-authored external opportunity reference');
  await page.getByLabel('Location (optional)').fill('Synthetic city');
  await page
    .getByRole('combobox', { name: 'Work arrangement', exact: true })
    .selectOption('REMOTE');
  await page.getByLabel('Application/submission deadline (date only, optional)').fill('2027-03-01');
  await page.getByLabel('Source platform or organization (optional)').fill('Synthetic platform');
  await page
    .getByLabel('Original opportunity URL (optional)')
    .fill('https://example.invalid/opportunity?ref=manual#details');
  for (const [index, name] of ['Python', 'FastAPI', 'Computer Vision'].entries()) {
    await page.getByRole('button', { name: 'Add required skill', exact: true }).click();
    await page.getByLabel(`Skill ${index + 1} name`, { exact: true }).fill(name);
  }
  await page
    .getByLabel('Requirements description (optional)')
    .fill('Manually entered skills, not legal interpretation.');
  expect(writes).toHaveLength(0);
  const created = page.waitForResponse(
    (r) => r.url().endsWith('/opportunities') && r.request().method() === 'POST',
  );
  await page.getByRole('button', { name: 'Save Opportunity', exact: true }).click();
  const response = await created;
  expect(response.status()).toBe(201);
  const record = (await response.json()).record;
  expect(record.opportunity.source_origin).toBe('manual_operator_entry');
  expect(record.opportunity.verification_status).toBe('NOT_VERIFIED');
  expect(record.opportunity.required_skills).toEqual(['Python', 'FastAPI', 'Computer Vision']);
  await expect(page.getByText(/Server confirmed CREATED/)).toBeVisible();
  const source = page.getByRole('link', { name: 'Open original opportunity' });
  await expect(source).toHaveAttribute(
    'href',
    'https://example.invalid/opportunity?ref=manual#details',
  );
  await expect(source).toHaveAttribute('rel', 'noopener noreferrer');
  await page.reload();
  await page
    .getByRole('button', { name: 'Open Synthetic external Python project', exact: true })
    .click();
  await expect(page.getByLabel('Skill 3 name', { exact: true })).toHaveValue('Computer Vision');
  await page.getByLabel('Opportunity title', { exact: true }).fill('Synthetic revised project');
  await page.getByRole('button', { name: 'Remove skill 3', exact: true }).click();
  await page.getByLabel('Skill 2 name', { exact: true }).fill('Data Engineering');
  page.once('dialog', (dialog) => dialog.dismiss());
  await page
    .getByRole('button', { name: 'Open Synthetic external Python project', exact: true })
    .click();
  await expect(page.getByLabel('Opportunity title', { exact: true })).toHaveValue(
    'Synthetic revised project',
  );
  expect(writes).toHaveLength(1);
  const updated = page.waitForResponse(
    (r) =>
      r.url().endsWith(`/opportunities/${record.opportunity_id}`) && r.request().method() === 'PUT',
  );
  await page.getByRole('button', { name: 'Save Opportunity', exact: true }).click();
  const update = await updated;
  expect(update.status()).toBe(200);
  expect(update.request().postDataJSON().expected_revision).toBe(1);
  expect((await update.json()).record.revision).toBe(2);
  await expect(page.getByText(/Server confirmed UPDATED/)).toBeVisible();
  await page.reload();
  await page.getByRole('button', { name: 'Open Synthetic revised project', exact: true }).click();
  await expect(page.getByLabel('Skill 2 name', { exact: true })).toHaveValue('Data Engineering');
  await expect(page.getByLabel('Skill 3 name', { exact: true })).toHaveCount(0);
  await expect(page.getByText(/Saved revision: 2/)).toBeVisible();
  expect(writes).toEqual(['POST', 'PUT']);
  expect(errors).toEqual([]);
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.screenshot({
    path: test.info().outputPath('native-synthetic-opportunity.png'),
    fullPage: true,
  });
});
