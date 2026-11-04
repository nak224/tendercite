import { test, expect } from '@playwright/test';

test('controlled daily Open Data → local search → import → edits → duplicate → matching → pipeline', async ({
  page,
  context,
  request,
  baseURL,
}) => {
  test.skip(
    process.env.TENDERCITE_CONTROLLED_DISCOVERY_SMOKE !== '1',
    'Requires controlled provider server and a fresh disposable database',
  );
  const api = `${baseURL}/tendercite-api/api/v1`;
  for (const resource of ['opportunities', 'profiles', 'pipeline']) {
    const r = await request.get(`${api}/${resource}`);
    expect(r.status()).toBe(200);
    expect((await r.json())[resource === 'pipeline' ? 'entries' : resource]).toEqual([]);
  }
  const calls: { path: string; body: unknown }[] = [],
    errors: string[] = [];
  context.on('request', (r) => {
    expect(new URL(r.url()).origin).toBe(baseURL);
    if (r.url().includes('/discovery/') && r.method() === 'POST')
      calls.push({ path: new URL(r.url()).pathname, body: r.postDataJSON() });
  });
  page.on('pageerror', (e) => errors.push(e.message));
  await page.goto('/discover');
  await page.getByLabel('Discovery source').selectOption('BKMS');
  await expect(page.getByText(/No daily catalog loaded/)).toBeVisible();
  expect(calls).toHaveLength(0);
  await page.getByLabel('Publication day (Europe/Berlin)').fill('2026-10-30');
  await page.getByRole('button', { name: 'Load publication day' }).click();
  await expect(page.getByRole('complementary', { name: 'Loaded German catalog' })).toContainText(
    '6 notices',
  );
  expect(calls).toHaveLength(1);
  await page.getByLabel('Keyword phrase').fill('Software');
  await page.getByLabel('Results per page').selectOption('5');
  expect(calls).toHaveLength(1);
  await page.getByRole('button', { name: 'Search local notices' }).click();
  await expect(
    page.getByRole('region', { name: 'German procurement search results' }),
  ).toContainText('6 matches');
  await page.getByRole('button', { name: 'Next results' }).click();
  await expect(
    page.getByText('Bekanntmachungsservice · 10000006-1', { exact: true }),
  ).toBeVisible();
  await page.getByRole('button', { name: 'Previous results' }).click();
  const first = page.locator('.discovery-results > li').first();
  // Refuse a live/misconfigured provider before any native import.
  await expect(first).toContainText('Bekanntmachungsservice · 10000001-1');
  await first.getByText('Inspect German notice', { exact: true }).click();
  await expect(first.getByRole('link', { name: 'Original German publication' })).toHaveAttribute(
    'href',
    'https://oeffentlichevergabe.de/ui/notices/10000001?noticeVersion=1',
  );
  expect(calls.filter((c) => c.path.endsWith('/import'))).toHaveLength(0);
  const response = page.waitForResponse((r) => r.url().endsWith('/discovery/bkms/import'));
  await first.getByRole('button', { name: 'Save to Opportunities' }).click();
  const saved = await response;
  expect(saved.status()).toBe(201);
  const original = (await saved.json()).record;
  expect(original.opportunity.required_skills).toEqual([]);
  expect(original.opportunity.source_origin).toBe('bkms_import');
  await first.getByRole('link', { name: 'View Opportunity' }).click();
  await expect(
    page.getByRole('complementary', { name: 'German procurement import provenance' }),
  ).toContainText('10000001-1');
  await page.getByText('Original Open Data metadata', { exact: true }).click();
  await expect(
    page.getByText('Original title: Synthetic software procurement', { exact: true }),
  ).toBeVisible();
  await page.getByLabel('Opportunity title', { exact: true }).fill('My synthetic local edit');
  await page.getByRole('button', { name: 'Add required skill' }).click();
  await page.getByLabel('Skill 1 name', { exact: true }).fill('Python');
  await page.getByRole('button', { name: 'Save Opportunity', exact: true }).click();
  await expect(page.getByText(/Server confirmed UPDATED/)).toBeVisible();
  await page
    .getByRole('navigation', { name: 'Primary navigation' })
    .getByRole('link', { name: 'Discover Opportunities', exact: true })
    .click();
  await page.getByLabel('Discovery source').selectOption('BKMS');
  await expect(page.getByRole('complementary', { name: 'Loaded German catalog' })).toContainText(
    '6 notices',
  );
  await page.getByLabel('Keyword phrase').fill('Software');
  await page.getByRole('button', { name: 'Search local notices' }).click();
  const repeated = page.waitForResponse((r) => r.url().endsWith('/discovery/bkms/import'));
  const again = page.locator('.discovery-results > li').first();
  await again.getByRole('button', { name: 'Save to Opportunities' }).click();
  const reply = await repeated;
  expect(reply.status()).toBe(200);
  const duplicate = await reply.json();
  expect(duplicate.status).toBe('ALREADY_PRESENT');
  expect(duplicate.record.revision).toBe(2);
  expect(duplicate.record.opportunity.title).toBe('My synthetic local edit');
  expect(duplicate.record.opportunity.external_source).toEqual(
    original.opportunity.external_source,
  );
  expect(calls.filter((c) => c.path.endsWith('/load'))).toHaveLength(1);
  await expect(again.getByText(/Server confirmed ALREADY_PRESENT/)).toBeVisible();
  await page.evaluate(() => scrollTo(0, 0));
  await page.screenshot({
    path: test.info().outputPath('german-discovery-controlled.png'),
    fullPage: true,
  });
  const profile = await context.newPage();
  await profile.goto('/profiles');
  profile.on('pageerror', (error) => errors.push(error.message));
  await profile.getByRole('button', { name: 'Create profile', exact: true }).click();
  await profile
    .getByLabel('Profile name', { exact: true })
    .fill('Synthetic German discovery profile');
  await profile.getByRole('button', { name: 'Add skill', exact: true }).click();
  await profile.getByLabel('Skill 1 name', { exact: true }).fill('Python');
  await profile.getByRole('button', { name: 'Save profile', exact: true }).click();
  await expect(profile.getByText(/Server confirmed CREATED/)).toBeVisible();
  await profile.close();
  await again.getByRole('link', { name: 'Compare Skills' }).click();
  await page
    .getByRole('combobox', { name: 'Profile', exact: true })
    .selectOption({ label: 'Synthetic German discovery profile · FREELANCER · revision 1' });
  await page.getByRole('button', { name: 'Compare Skills', exact: true }).click();
  await expect(
    page.getByText('1 of 1 requested skill labels are recorded in this profile.'),
  ).toBeVisible();
  await page.getByRole('link', { name: 'Track selected opportunity', exact: true }).click();
  await page.getByRole('button', { name: 'Add to Pipeline', exact: true }).click();
  await expect(page.getByText(/Server confirmed CREATED/)).toBeVisible();
  expect((await (await request.get(`${api}/pipeline`)).json()).entries).toHaveLength(1);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/discover');
  await page.getByLabel('Discovery source').selectOption('BKMS');
  await expect(page.getByRole('complementary', { name: 'Loaded German catalog' })).toContainText(
    '6 notices',
  );
  await page.getByLabel('Keyword phrase').fill('Software');
  await page.getByRole('button', { name: 'Search local notices' }).click();
  await expect(
    page.getByRole('region', { name: 'German procurement search results' }),
  ).toContainText('6 matches');
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.evaluate(() => scrollTo(0, 0));
  await page.screenshot({
    path: test.info().outputPath('german-discovery-mobile.png'),
    fullPage: true,
  });
  expect(errors).toEqual([]);
});
