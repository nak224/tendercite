import { expect, test } from '@playwright/test';

test('explicit eForms enrichment → individual lots/conflicts → native deadline remains independent', async ({
  page,
  context,
  request,
  baseURL,
}) => {
  test.skip(
    process.env.TENDERCITE_CONTROLLED_DISCOVERY_SMOKE !== '1',
    'Needs controlled synthetic exports and a fresh disposable database',
  );
  const api = `${baseURL}/tendercite-api/api/v1`;
  expect((await (await request.get(`${api}/opportunities`)).json()).opportunities).toEqual([]);
  expect((await (await request.get(`${api}/discovery/bkms/catalogs`)).json()).days).toEqual([]);
  const posts: { path: string; body: Record<string, unknown> }[] = [],
    errors: string[] = [];
  context.on('request', (r) => {
    expect(new URL(r.url()).origin).toBe(baseURL);
    if (r.method() === 'POST' && r.url().includes('/discovery/'))
      posts.push({ path: new URL(r.url()).pathname, body: r.postDataJSON() });
  });
  page.on('pageerror', (e) => errors.push(e.message));
  await page.goto('/discover');
  await page.getByLabel('Discovery source').selectOption('BKMS');
  await expect(page.getByText(/No daily catalog loaded/)).toBeVisible();
  expect(posts).toHaveLength(0);
  await page.getByLabel('Publication day (Europe/Berlin)').fill('2026-10-30');
  await page.getByRole('button', { name: 'Load publication day', exact: true }).click();
  await expect(page.getByRole('complementary', { name: 'Loaded German catalog' })).toContainText(
    '6 notices',
  );
  await page.getByLabel('Keyword phrase').fill('Software');
  await page.getByRole('button', { name: 'Search local notices' }).click();
  const first = page.locator('.discovery-results > li').first();
  await expect(first).toContainText('Bekanntmachungsservice · 10000001-1');
  await expect(first).toContainText('Synthetic software procurement');
  await expect(first).toContainText('Not enriched for this exact daily snapshot');
  expect(posts.filter((p) => p.path.endsWith('/deadlines/load'))).toHaveLength(0);
  await page
    .getByRole('button', { name: 'Load official deadlines for 2026-10-30', exact: true })
    .click();
  await expect(page.getByText(/Server confirmed official deadline enrichment/)).toBeVisible();
  await expect(page.getByText(/Stored deadline state: CURRENT/)).toContainText('MULTIPLE_LOTS: 1');
  await page.getByRole('button', { name: 'Search local notices' }).click();
  const source = first.getByRole('complementary', { name: 'Official eForms submission deadlines' });
  await expect(source).toContainText('Extraction: SINGLE');
  await expect(source).toContainText('2026-12-04+01:00');
  await expect(source).toContainText('10:00:00+01:00');
  const second = page
    .locator('.discovery-results > li')
    .filter({ hasText: 'Bekanntmachungsservice · 10000002-1' });
  await expect(second).toContainText('Extraction: MULTIPLE_LOTS');
  await expect(second).toContainText('2026-12-08+01:00');
  await expect(second).toContainText('2026-12-09+01:00');
  const third = page
    .locator('.discovery-results > li')
    .filter({ hasText: 'Bekanntmachungsservice · 10000003-1' });
  await expect(third).toContainText('OFFSET_CONFLICT');
  await expect(third).toContainText('No combined timestamp is asserted');
  await expect(
    page
      .locator('.discovery-results > li')
      .filter({ hasText: 'Bekanntmachungsservice · 10000004-1' }),
  ).toContainText('Extraction: MISSING');
  await expect(
    page
      .locator('.discovery-results > li')
      .filter({ hasText: 'Bekanntmachungsservice · 10000005-1' }),
  ).toContainText('UNSUPPORTED_CUSTOMIZATION');
  await expect(first.getByRole('link', { name: 'Original German publication' })).toHaveAttribute(
    'href',
    'https://oeffentlichevergabe.de/ui/notices/10000001?noticeVersion=1',
  );
  await page.evaluate(() => scrollTo(0, 0));
  await page.screenshot({
    path: test.info().outputPath('official-deadlines-desktop.png'),
    fullPage: true,
  });
  const saved = page.waitForResponse((r) => r.url().endsWith('/discovery/bkms/import'));
  await first.getByRole('button', { name: 'Save to Opportunities' }).click();
  const native = await (await saved).json();
  expect(native.record.opportunity.deadline).toBe('2026-12-04');
  expect(native.record.opportunity.external_source.original_metadata.deadline).toBe(
    '2026-12-04T12:00:00+01:00',
  );
  await first.getByRole('link', { name: 'View Opportunity' }).click();
  await page
    .getByLabel('Application/submission deadline (date only, optional)', { exact: true })
    .fill('2027-01-31');
  await page.getByRole('button', { name: 'Save Opportunity', exact: true }).click();
  await expect(page.getByText(/Server confirmed UPDATED/)).toBeVisible();
  await page.goto('/discover');
  await page.getByLabel('Discovery source').selectOption('BKMS');
  await page
    .getByRole('button', { name: 'Read stored deadlines for 2026-10-30', exact: true })
    .click();
  await expect(page.getByText(/Stored deadline state: CURRENT/)).toBeVisible();
  await page
    .getByRole('button', { name: 'Load official deadlines for 2026-10-30', exact: true })
    .click();
  await expect(page.getByText(/Server confirmed official deadline enrichment/)).toBeVisible();
  const persisted = await (
    await request.get(`${api}/opportunities/${native.record.opportunity_id}`)
  ).json();
  expect(persisted.revision).toBe(2);
  expect(persisted.opportunity.deadline).toBe('2027-01-31');
  expect(persisted.opportunity.external_source).toEqual(native.record.opportunity.external_source);
  const loads = posts.filter((p) => p.path.endsWith('/deadlines/load'));
  expect(loads).toHaveLength(2);
  expect(loads[1]!.body.expected_enrichment_id).toMatch(/^[a-f0-9]{64}$/);
  await page.getByLabel('Keyword phrase').fill('Software');
  await page.getByRole('button', { name: 'Search local notices' }).click();
  await page.setViewportSize({ width: 390, height: 844 });
  await second.getByText('Deadline extraction provenance', { exact: true }).click();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({
    path: test.info().outputPath('official-deadlines-mobile.png'),
    fullPage: true,
  });
  expect(errors).toEqual([]);
});
