import { test, expect } from '@playwright/test';

test('native profile → five manual skills → save → reload → edit → save', async ({
  page,
  request,
  baseURL,
}) => {
  test.skip(
    process.env.TENDERCITE_SYNTHETIC_SMOKE !== '1',
    'Requires a fresh disposable synthetic database',
  );
  const api = `${baseURL}/tendercite-api/api/v1`;
  const initial = await request.get(`${api}/profiles`);
  expect(initial.status()).toBe(200);
  expect((await initial.json()).profiles).toEqual([]);
  const writes: string[] = [];
  const errors: string[] = [];
  page.on('pageerror', (error) => errors.push(error.message));
  page.on('request', (sent) => {
    expect(new URL(sent.url()).origin).toBe(baseURL);
    const path = new URL(sent.url()).pathname;
    if (path.startsWith('/tendercite-api/')) {
      expect(path).toMatch(/^\/tendercite-api\/api\/v1\/profiles(?:\/[a-f0-9-]+)?$/);
      if (sent.method() !== 'GET') writes.push(sent.method());
    }
  });
  await page.goto('/profiles');
  await expect(page.getByText('No saved profiles yet.')).toBeVisible();
  await page.getByRole('button', { name: 'Create profile' }).click();
  await page.getByLabel('Profile name', { exact: true }).fill('Synthetic freelancer');
  await page
    .getByLabel('Headline or short description')
    .fill('Synthetic software and data services');
  await page.getByLabel('Location (optional)').fill('Synthetic city');
  await page.getByLabel('Preferred work arrangement').selectOption('REMOTE');
  const skills = ['Python', 'Machine Learning', 'Computer Vision', 'React', 'Project Management'];
  for (const [index, name] of skills.entries()) {
    await page.getByRole('button', { name: 'Add skill', exact: true }).click();
    await page.getByLabel(`Skill ${index + 1} name`, { exact: true }).fill(name);
  }
  await page
    .getByLabel('Opportunity keywords (optional, one per line)')
    .fill('Software Development\nConsulting');
  expect(writes).toHaveLength(0);
  const created = page.waitForResponse(
    (r) => r.url().endsWith('/profiles') && r.request().method() === 'POST',
  );
  await page.getByRole('button', { name: 'Save profile', exact: true }).click();
  const response = await created;
  expect(response.status()).toBe(201);
  const record = (await response.json()).record;
  expect(record.profile.source_origin).toBe('manual_operator_entry');
  expect(record.profile.skills).toHaveLength(5);
  expect(
    record.profile.skills.every(
      (s: { verification_status: string }) => s.verification_status === 'NOT_VERIFIED',
    ),
  ).toBe(true);
  await expect(page.getByText(/Server confirmed CREATED/)).toBeVisible();
  await page.reload();
  await page.getByRole('button', { name: 'Open Synthetic freelancer', exact: true }).click();
  await expect(page.getByLabel('Skill 3 name', { exact: true })).toHaveValue('Computer Vision');
  await page.getByLabel('Profile name', { exact: true }).fill('Synthetic revised freelancer');
  await page.getByLabel('Skill 1 name', { exact: true }).fill('Python development');
  await page.getByRole('button', { name: 'Remove skill 3', exact: true }).click();
  await page.getByRole('button', { name: 'Add skill', exact: true }).click();
  await page.getByLabel('Skill 5 name', { exact: true }).fill('Data Engineering');
  expect(writes).toHaveLength(1);
  const updated = page.waitForResponse(
    (r) => r.url().endsWith(`/profiles/${record.profile_id}`) && r.request().method() === 'PUT',
  );
  await page.getByRole('button', { name: 'Save profile', exact: true }).click();
  const update = await updated;
  expect(update.status()).toBe(200);
  expect(update.request().postDataJSON().expected_revision).toBe(1);
  const next = (await update.json()).record;
  expect(next.revision).toBe(2);
  expect(next.profile.skills[0].skill_id).toBe(record.profile.skills[0].skill_id);
  await expect(page.getByText(/Server confirmed UPDATED/)).toBeVisible();
  await page.reload();
  await page
    .getByRole('button', { name: 'Open Synthetic revised freelancer', exact: true })
    .click();
  await expect(page.getByLabel('Skill 1 name', { exact: true })).toHaveValue('Python development');
  await expect(page.getByLabel('Skill 5 name', { exact: true })).toHaveValue('Data Engineering');
  await expect(page.getByText(/Saved revision: 2/)).toBeVisible();
  expect(writes).toEqual(['POST', 'PUT']);
  expect(errors).toEqual([]);
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.screenshot({
    path: test.info().outputPath('native-synthetic-profile.png'),
    fullPage: true,
  });
});
