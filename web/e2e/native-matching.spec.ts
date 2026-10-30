import { test, expect } from '@playwright/test';

test('saved native declarations → explicit comparison → stale revision → refresh → compare', async ({
  page,
  context,
  request,
  baseURL,
}) => {
  test.skip(
    process.env.TENDERCITE_NATIVE_MATCHING_SMOKE !== '1',
    'Requires its own fresh disposable synthetic database',
  );
  const api = `${baseURL}/tendercite-api/api/v1`;
  for (const resource of ['profiles', 'opportunities']) {
    const initial = await request.get(`${api}/${resource}`);
    expect(initial.status()).toBe(200);
    expect((await initial.json())[resource]).toEqual([]);
  }
  const errors: string[] = [];
  const previews: unknown[] = [];
  const writes: string[] = [];
  page.on('pageerror', (e) => errors.push(e.message));
  page.on('request', (sent) => {
    expect(new URL(sent.url()).origin).toBe(baseURL);
    if (sent.method() !== 'GET' && new URL(sent.url()).pathname.startsWith('/tendercite-api/')) {
      if (sent.url().endsWith('/native-matches/preview')) previews.push(sent.postDataJSON());
      else writes.push(sent.method());
    }
  });
  await page.goto('/profiles');
  await expect(page.getByText('No saved profiles yet.')).toBeVisible();
  await page.getByRole('button', { name: 'Create profile', exact: true }).click();
  await page.getByLabel('Profile name', { exact: true }).fill('Synthetic matching freelancer');
  for (const [index, label] of ['Python', 'Machine  Learning', 'Java'].entries()) {
    await page.getByRole('button', { name: 'Add skill', exact: true }).click();
    await page.getByLabel(`Skill ${index + 1} name`, { exact: true }).fill(label);
  }
  await page.getByRole('button', { name: 'Save profile', exact: true }).click();
  await expect(page.getByText(/Server confirmed CREATED/)).toBeVisible();
  await page.getByRole('link', { name: 'Opportunities', exact: true }).click();
  await expect(page.getByText('No saved opportunities yet.')).toBeVisible();
  await page.getByRole('button', { name: 'Create Opportunity', exact: true }).click();
  await page.getByLabel('Opportunity title', { exact: true }).fill('Synthetic matching project');
  await page.getByLabel('Organization or client (optional)').fill('Synthetic organization');
  for (const [index, label] of ['python', 'machine learning', 'JavaScript', 'FastAPI'].entries()) {
    await page.getByRole('button', { name: 'Add required skill', exact: true }).click();
    await page.getByLabel(`Skill ${index + 1} name`, { exact: true }).fill(label);
  }
  await page.getByRole('button', { name: 'Save Opportunity', exact: true }).click();
  await expect(page.getByText(/Server confirmed CREATED/)).toBeVisible();
  await page
    .getByRole('navigation', { name: 'Primary navigation' })
    .getByRole('link', { name: 'Opportunity Matching', exact: true })
    .click();
  const profile = page.getByRole('combobox', { name: 'Profile', exact: true });
  const opportunity = page.getByRole('combobox', { name: 'Opportunity', exact: true });
  await profile.selectOption({ label: 'Synthetic matching freelancer · FREELANCER · revision 1' });
  await opportunity.selectOption({
    label: 'Synthetic matching project · FREELANCE_PROJECT · Synthetic organization · revision 1',
  });
  expect(previews).toHaveLength(0);
  await page.getByRole('button', { name: 'Compare Skills', exact: true }).click();
  await expect(
    page.getByText('2 of 4 requested skill labels are recorded in this profile.'),
  ).toBeVisible();
  const table = page.getByRole('table');
  await expect(table.getByRole('row', { name: /JavaScript/ })).toContainText('NOT_RECORDED');
  await expect(table.getByRole('row', { name: /machine learning/ })).toContainText('MATCHED');
  const other = await context.newPage();
  await other.goto('/profiles');
  await other
    .getByRole('button', { name: 'Open Synthetic matching freelancer', exact: true })
    .click();
  await other.getByRole('button', { name: 'Add skill', exact: true }).click();
  await other.getByLabel('Skill 4 name', { exact: true }).fill('FastAPI');
  await other.getByRole('button', { name: 'Save profile', exact: true }).click();
  await expect(other.getByText(/Server confirmed UPDATED/)).toBeVisible();
  await other.close();
  await page.getByRole('button', { name: 'Compare Skills', exact: true }).click();
  await expect(page.getByRole('alert')).toContainText('PROFILE_REVISION_CONFLICT');
  await expect(page.getByRole('button', { name: 'Compare Skills', exact: true })).toBeDisabled();
  expect(previews).toHaveLength(2);
  await page.getByRole('button', { name: 'Refresh saved records', exact: true }).click();
  await profile.selectOption({ label: 'Synthetic matching freelancer · FREELANCER · revision 2' });
  await opportunity.selectOption({
    label: 'Synthetic matching project · FREELANCE_PROJECT · Synthetic organization · revision 1',
  });
  await page.getByRole('button', { name: 'Compare Skills', exact: true }).click();
  await expect(
    page.getByText('3 of 4 requested skill labels are recorded in this profile.'),
  ).toBeVisible();
  expect(previews).toHaveLength(3);
  expect(previews[2]).toMatchObject({
    expected_profile_revision: 2,
    expected_opportunity_revision: 1,
  });
  expect(writes).toEqual(['POST', 'POST']);
  expect(errors).toEqual([]);
  expect(await page.evaluate(() => localStorage.length + sessionStorage.length)).toBe(0);
  await page.screenshot({
    path: test.info().outputPath('native-matching-synthetic.png'),
    fullPage: true,
  });
});
