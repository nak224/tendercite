import { test, expect } from '@playwright/test';

test('controlled TED search → inspect → import → local edits → duplicate → matching → pipeline', async ({
  page,
  context,
  request,
  baseURL,
}) => {
  test.skip(
    process.env.TENDERCITE_CONTROLLED_DISCOVERY_SMOKE !== '1',
    'Requires the controlled synthetic provider server and a fresh database',
  );
  const api = `${baseURL}/tendercite-api/api/v1`;
  for (const resource of ['opportunities', 'profiles', 'pipeline']) {
    const response = await request.get(`${api}/${resource}`);
    expect(response.status()).toBe(200);
    expect((await response.json())[resource === 'pipeline' ? 'entries' : resource]).toEqual([]);
  }
  const searches: Record<string, unknown>[] = [],
    imports: Record<string, unknown>[] = [],
    errors: string[] = [];
  context.on('request', (sent) => {
    expect(new URL(sent.url()).origin).toBe(baseURL);
    const path = new URL(sent.url()).pathname;
    if (path.endsWith('/discovery/ted/search')) searches.push(sent.postDataJSON());
    if (path.endsWith('/discovery/ted/import')) imports.push(sent.postDataJSON());
  });
  page.on('pageerror', (error) => errors.push(error.message));
  const nav = page.getByRole('navigation', { name: 'Primary navigation' });
  await page.goto('/discover');
  expect(searches).toHaveLength(0);
  await page.getByLabel('Keyword phrase').fill('Software');
  await page.getByRole('combobox', { name: 'Results per page', exact: true }).selectOption('5');
  expect(searches).toHaveLength(0);
  await page.getByRole('button', { name: 'Search', exact: true }).click();
  await expect(
    page.getByRole('heading', { name: 'Synthetic software services', exact: true }),
  ).toBeVisible();
  // Stop before importing if the operator accidentally ran against a live provider.
  await expect(page.getByText('TED · 99999991-2099', { exact: true })).toBeVisible();
  expect(imports).toHaveLength(0);
  await page.getByRole('button', { name: 'Next results', exact: true }).click();
  await expect(
    page.getByRole('heading', { name: 'Fictional software notice 6', exact: true }),
  ).toBeVisible();
  expect(searches[1]).toMatchObject({ page: 2, limit: 5, keyword: 'Software' });
  await page.getByRole('button', { name: 'Previous results', exact: true }).click();
  const first = page.locator('.discovery-results > li').filter({
    has: page.getByRole('heading', { name: 'Synthetic software services', exact: true }),
  });
  await first.getByText('Inspect TED result', { exact: true }).click();
  await expect(first.getByText('Publication date: 2099-02-01', { exact: true })).toBeVisible();
  await expect(
    first.getByRole('link', { name: 'Original TED publication', exact: true }),
  ).toHaveAttribute('href', 'https://ted.europa.eu/de/notice/-/detail/99999991-2099');
  const imported = page.waitForResponse((r) => r.url().endsWith('/discovery/ted/import'));
  await first.getByRole('button', { name: 'Save to Opportunities', exact: true }).click();
  const firstImport = await imported;
  expect(firstImport.status()).toBe(201);
  const record = (await firstImport.json()).record;
  expect(record.opportunity.source_origin).toBe('ted_import');
  expect(record.opportunity.required_skills).toEqual([]);
  expect(imports).toEqual([
    { schema_version: 'ted-import-input-1', publication_id: '99999991-2099' },
  ]);
  await first.getByRole('link', { name: 'View Opportunity', exact: true }).click();
  await expect(page.getByRole('complementary', { name: 'TED import provenance' })).toContainText(
    '99999991-2099',
  );
  await page
    .getByLabel('Opportunity title', { exact: true })
    .fill('Locally edited synthetic opportunity');
  await page.getByRole('button', { name: 'Add required skill', exact: true }).click();
  await page.getByLabel('Skill 1 name', { exact: true }).fill('Python');
  await page.getByRole('button', { name: 'Save Opportunity', exact: true }).click();
  await expect(page.getByText(/Server confirmed UPDATED/)).toBeVisible();
  await nav.getByRole('link', { name: 'Discover Opportunities', exact: true }).click();
  await page.getByLabel('Keyword phrase').fill('Software');
  await page.getByRole('button', { name: 'Search', exact: true }).click();
  const again = page.locator('.discovery-results > li').filter({
    has: page.getByRole('heading', { name: 'Synthetic software services', exact: true }),
  });
  const duplicate = page.waitForResponse((r) => r.url().endsWith('/discovery/ted/import'));
  await again.getByRole('button', { name: 'Save to Opportunities', exact: true }).click();
  const repeated = await duplicate;
  expect(repeated.status()).toBe(200);
  const duplicateBody = await repeated.json();
  expect(duplicateBody.status).toBe('ALREADY_PRESENT');
  expect(duplicateBody.record.revision).toBe(2);
  expect(duplicateBody.record.opportunity.title).toBe('Locally edited synthetic opportunity');
  expect(duplicateBody.record.opportunity.external_source).toEqual(
    record.opportunity.external_source,
  );
  await expect(again.getByText(/Server confirmed ALREADY_PRESENT/)).toBeVisible();
  await page.evaluate(() => scrollTo(0, 0));
  await page.screenshot({
    path: test.info().outputPath('ted-discovery-controlled.png'),
    fullPage: true,
  });
  const profile = await context.newPage();
  profile.on('pageerror', (error) => errors.push(error.message));
  await profile.goto('/profiles');
  await profile.getByRole('button', { name: 'Create profile', exact: true }).click();
  await profile.getByLabel('Profile name', { exact: true }).fill('Fictional discovery freelancer');
  await profile.getByRole('button', { name: 'Add skill', exact: true }).click();
  await profile.getByLabel('Skill 1 name', { exact: true }).fill('Python');
  await profile.getByRole('button', { name: 'Save profile', exact: true }).click();
  await expect(profile.getByText(/Server confirmed CREATED/)).toBeVisible();
  await profile.close();
  await again.getByRole('link', { name: 'Compare Skills', exact: true }).click();
  await page
    .getByRole('combobox', { name: 'Profile', exact: true })
    .selectOption({ label: 'Fictional discovery freelancer · FREELANCER · revision 1' });
  await page.getByRole('button', { name: 'Compare Skills', exact: true }).click();
  await expect(
    page.getByText('1 of 1 requested skill labels are recorded in this profile.'),
  ).toBeVisible();
  await page.getByRole('link', { name: 'Track selected opportunity', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Add to Pipeline', exact: true })).toBeEnabled();
  expect((await (await request.get(`${api}/pipeline`)).json()).entries).toEqual([]);
  await page.getByRole('button', { name: 'Add to Pipeline', exact: true }).click();
  await expect(page.getByRole('region', { name: 'Pipeline editor' })).toContainText(
    'Locally edited synthetic opportunity',
  );
  await expect(page.getByRole('combobox', { name: 'Stage', exact: true })).toHaveValue('SAVED');
  const saved = await (await request.get(`${api}/opportunities/${record.opportunity_id}`)).json();
  expect(saved.opportunity.source_origin).toBe('ted_import');
  expect(saved.opportunity.required_skills).toEqual(['Python']);
  expect((await (await request.get(`${api}/opportunities`)).json()).opportunities).toHaveLength(1);
  expect(imports).toHaveLength(2);
  expect(errors).toEqual([]);
  expect(await page.evaluate(() => localStorage.length + sessionStorage.length)).toBe(0);
});
