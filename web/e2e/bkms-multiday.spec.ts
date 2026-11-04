import { test, expect } from '@playwright/test';

test('two controlled daily exports → combined versions/types → native import survives catalog changes', async ({
  page,
  context,
  request,
  baseURL,
}) => {
  test.skip(
    process.env.TENDERCITE_CONTROLLED_DISCOVERY_SMOKE !== '1',
    'Requires controlled synthetic exports and a fresh disposable database',
  );
  const api = `${baseURL}/tendercite-api/api/v1`;
  expect((await (await request.get(`${api}/opportunities`)).json()).opportunities).toEqual([]);
  expect((await (await request.get(`${api}/discovery/bkms/catalogs`)).json()).days).toEqual([]);
  const calls: { path: string; body: Record<string, unknown> }[] = [],
    errors: string[] = [];
  context.on('request', (sent) => {
    expect(new URL(sent.url()).origin).toBe(baseURL);
    if (sent.url().includes('/discovery/') && sent.method() === 'POST')
      calls.push({ path: new URL(sent.url()).pathname, body: sent.postDataJSON() });
  });
  page.on('pageerror', (error) => errors.push(error.message));
  await page.goto('/discover');
  await page.getByLabel('Discovery source').selectOption('BKMS');
  await expect(page.getByText(/No daily catalog loaded/)).toBeVisible();
  expect(calls).toHaveLength(0);
  for (const day of ['2026-10-30', '2026-10-31']) {
    await page.getByLabel('Publication day (Europe/Berlin)').fill(day);
    await page.getByRole('button', { name: 'Load publication day' }).click();
    await expect(page.getByRole('button', { name: `Remove ${day}`, exact: true })).toBeVisible();
  }
  const coverage = page.getByRole('complementary', { name: 'Loaded German catalog' });
  await expect(coverage).toContainText('Stored publication days (2 / 7)');
  await expect(coverage).toContainText('2026-10-31, 2026-10-30');
  await expect(coverage).toContainText('6 notices');
  await expect(coverage).toContainText('3 notices');
  const collection = await (await request.get(`${api}/discovery/bkms/catalogs`)).json();
  expect(
    collection.days.map((d: { catalog: { export_day: string } }) => d.catalog.export_day),
  ).toEqual(['2026-10-31', '2026-10-30']);
  const loads = () => calls.filter((c) => c.path.endsWith('/load'));
  expect(loads()).toHaveLength(2);
  await page.getByLabel('Keyword phrase').fill('Software');
  await page.getByLabel('Results per page').selectOption('5');
  expect(calls).toHaveLength(2);
  const results = page.getByRole('region', { name: 'German procurement search results' });
  await page.getByRole('button', { name: 'Search local notices' }).click();
  await expect(results).toContainText('7 matches');
  let first = page.locator('.discovery-results > li').first();
  // Refuse a wrong/live provider before importing or editing anything.
  await expect(first).toContainText('Bekanntmachungsservice · 10000001-2');
  await expect(first).toContainText('2 distinct stored version label(s)');
  await expect(first).toContainText('Unavailable in OCDS');
  await expect(page.getByText('Bekanntmachungsservice · 10000001-1', { exact: true })).toHaveCount(
    0,
  );
  const duplicate = page.locator('.discovery-results > li').filter({ hasText: '10000002-1' });
  await duplicate.getByText('Inspect German notice', { exact: true }).click();
  await expect(duplicate).toContainText('Observed in 2 retained daily export(s)');
  await page.getByRole('button', { name: 'Next results' }).click();
  await expect(results).toContainText('page 2');
  await expect(page.locator('.discovery-results > li')).toHaveCount(2);
  await page.getByRole('button', { name: 'Previous results' }).click();
  await expect(results).toContainText('page 1');
  await page.getByLabel('Notice type').selectOption('AWARD');
  await page.getByRole('button', { name: 'Search local notices' }).click();
  await expect(results).toContainText('1 matches');
  await expect(results).toContainText('Source tags: award');
  await page.getByLabel('Notice type').selectOption('OTHER_UNKNOWN');
  await page.getByRole('button', { name: 'Search local notices' }).click();
  await expect(results).toContainText('1 matches');
  await expect(results).toContainText('Unknown / not supplied');
  await page.getByLabel('Notice type').selectOption('ALL');
  await page.getByLabel('Show all stored notice versions').check();
  await page.getByLabel('Results per page').selectOption('10');
  await page.getByRole('button', { name: 'Search local notices' }).click();
  await expect(results).toContainText('8 matches');
  await expect(
    page.getByText('Bekanntmachungsservice · 10000001-1', { exact: true }),
  ).toBeVisible();
  await page.screenshot({ path: test.info().outputPath('multiday-desktop.png'), fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  await page
    .locator('.discovery-results > li')
    .filter({ hasText: 'Bekanntmachungsservice · 10000002-1' })
    .getByText('Inspect German notice', { exact: true })
    .click();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: test.info().outputPath('multiday-mobile.png'), fullPage: true });
  await page.setViewportSize({ width: 1440, height: 1100 });
  await page.getByLabel('Show all stored notice versions').uncheck();
  await page.getByRole('button', { name: 'Search local notices' }).click();
  const saved = page.waitForResponse((r) => r.url().endsWith('/discovery/bkms/import'));
  first = page.locator('.discovery-results > li').first();
  await first.getByRole('button', { name: 'Save to Opportunities' }).click();
  const response = await saved;
  expect(response.status()).toBe(201);
  const original = (await response.json()).record;
  expect(original.opportunity.external_source.export_day).toBe('2026-10-31');
  expect(original.opportunity.deadline).toBeNull();
  await first.getByRole('link', { name: 'View Opportunity' }).click();
  await page.getByLabel('Opportunity title', { exact: true }).fill('Synthetic two-day local edit');
  await page.getByRole('button', { name: 'Add required skill' }).click();
  await page.getByLabel('Skill 1 name', { exact: true }).fill('Python');
  await page.getByRole('button', { name: 'Save Opportunity', exact: true }).click();
  await expect(page.getByText(/Server confirmed UPDATED/)).toBeVisible();
  await page.goto('/discover');
  await page.getByLabel('Discovery source').selectOption('BKMS');
  await expect(coverage).toContainText('Stored publication days (2 / 7)');
  await page.getByLabel('Publication day (Europe/Berlin)').fill('2026-10-31');
  await page.getByRole('button', { name: 'Replace stored day' }).click();
  expect(loads()).toHaveLength(2);
  await page.getByRole('button', { name: 'Confirm day replacement' }).click();
  await expect(page.getByText(/Server confirmed replacement/)).toBeVisible();
  expect(loads()).toHaveLength(3);
  expect(loads()[2]!.body.expected_catalog_id).toBe(collection.days[0].catalog.catalog_id);
  await page.getByRole('button', { name: 'Remove 2026-10-31', exact: true }).click();
  await page.getByRole('button', { name: 'Confirm day removal' }).click();
  await expect(coverage).toContainText('Stored publication days (1 / 7)');
  await expect(coverage).toContainText('6 notices');
  const persisted = (await (await request.get(`${api}/opportunities`)).json()).opportunities;
  expect(persisted).toHaveLength(1);
  expect(persisted[0].opportunity_id).toBe(original.opportunity_id);
  expect(persisted[0].revision).toBe(2);
  const detail = await (
    await request.get(`${api}/opportunities/${original.opportunity_id}`)
  ).json();
  expect(detail.opportunity.title).toBe('Synthetic two-day local edit');
  expect(detail.opportunity.external_source).toEqual(original.opportunity.external_source);
  await page.getByLabel('Keyword phrase').fill('Software');
  await page.getByRole('button', { name: 'Search local notices' }).click();
  await expect(results).toContainText('6 matches');
  await expect(
    page.getByText('Bekanntmachungsservice · 10000001-1', { exact: true }),
  ).toBeVisible();
  expect(errors).toEqual([]);
  expect(loads()).toHaveLength(3);
  expect(calls.filter((c) => c.path.endsWith('/import'))).toHaveLength(1);
  expect(calls.filter((c) => c.path.endsWith('/remove'))).toHaveLength(1);
});
