import { readFileSync } from 'node:fs';
import { test, expect } from '@playwright/test';
import { syntheticPdf } from '../src/test/tenderPdf';

// Opt-in only. Start the backend with a NEW disposable data directory, retrieval disabled,
// no provider configuration and the snapshot flag enabled. No TCE service is used.
test('standalone PDF → intake → human review → assessment → snapshot import', async ({
  page,
  request,
  baseURL,
}) => {
  test.skip(
    process.env.TENDERCITE_SYNTHETIC_SMOKE !== '1',
    'Requires explicit disposable synthetic setup',
  );
  const api = `${baseURL}/tendercite-api/api/v1`;
  const configuration = await request.get(`${api}/configuration`);
  expect(configuration.ok()).toBe(true);
  expect((await configuration.json()).retrieval_enabled).toBe(false);
  expect((await configuration.json()).llm_configured).toBe(false);
  // Stop before any mutation if the operator pointed this test at an existing corpus.
  expect(await (await request.get(`${api}/documents`)).json()).toEqual([]);
  expect(await (await request.get(`${api}/findings`)).json()).toEqual([]);
  const mutations: string[] = [];
  const pageErrors: string[] = [];
  page.on('pageerror', (error) => pageErrors.push(error.message));
  page.on('request', (sent) => {
    expect(new URL(sent.url()).origin).toBe(baseURL);
    if (sent.method() !== 'GET') {
      expect(sent.url()).toMatch(
        /\/api\/v1\/(documents|curated-intakes|findings|go-no-go|capability-snapshots)/,
      );
      mutations.push(new URL(sent.url()).pathname);
    }
  });
  await page.goto('/');
  await expect(page.getByText('No documents available.', { exact: true })).toBeVisible();
  const file = syntheticPdf('standalone-synthetic.pdf');
  await page.getByLabel('PDF files').setInputFiles({
    name: file.name,
    mimeType: file.type,
    buffer: Buffer.from(await file.arrayBuffer()),
  });
  const uploaded = page.waitForResponse(
    (reply) => reply.url().endsWith('/documents') && reply.request().method() === 'POST',
  );
  await page.getByRole('button', { name: 'Upload', exact: true }).click();
  const document = await (await uploaded).json();
  expect(document.page_count).toBe(1);
  await expect(page.getByRole('list', { name: 'Upload results' })).toContainText('Accepted');
  await page.getByRole('link', { name: 'Create manual requirements' }).click();
  await page.getByLabel('Intake purpose').fill('Disposable standalone migration smoke');
  const requirement = page.getByRole('group', { name: 'Requirement 1', exact: true });
  await requirement.getByLabel('Requirement statement').fill('This is a synthetic tender example.');
  await requirement
    .getByRole('combobox', { name: 'Category', exact: true })
    .selectOption('INFORMATION');
  await requirement
    .getByRole('combobox', { name: 'Requirement type', exact: true })
    .selectOption('OTHER');
  await requirement.getByRole('combobox', { name: 'Source document' }).selectOption(document.id);
  await requirement.getByLabel('Physical PDF page').fill('1');
  await requirement.getByLabel('Exact supporting quotation').fill('Synthetic tender');
  await page.getByRole('button', { name: 'Validate', exact: true }).click();
  await expect(page.getByRole('region', { name: 'Validated intake' })).toBeVisible();
  await page.getByRole('checkbox').check();
  await page.getByRole('button', { name: 'Save requirements' }).click();
  await expect(page.getByRole('region', { name: 'Saved intake' })).toContainText('UNREVIEWED');
  await page.getByRole('link', { name: 'Open analysis', exact: true }).click();
  await page
    .getByRole('link', { name: 'This is a synthetic tender example.', exact: true })
    .click();
  await expect(page.getByText(/Review: UNREVIEWED/)).toBeVisible();
  await expect(
    page.getByText('No review events. This finding has not been reviewed.'),
  ).toBeVisible();
  const review = page.getByRole('region', { name: 'Human finding review' });
  await review.getByRole('combobox', { name: 'Review action' }).selectOption('CONFIRM');
  await review
    .getByLabel('Review comment (optional)')
    .fill('Simulated review for synthetic migration testing only.');
  await review.getByRole('button', { name: 'Save Review' }).click();
  await expect(page.getByText(/Review: CONFIRMED/)).toBeVisible();
  await expect(page.getByRole('region', { name: 'Review history' })).toContainText(
    'Simulated review',
  );
  const source = page.getByRole('link', { name: 'View source PDF page 1' });
  const findingUrl = page.url();
  await source.click();
  await expect(page.locator('pre.source-page-text')).toHaveText('Synthetic tender');
  await page.goto(findingUrl);
  await page.getByRole('link', { name: /^Analysis / }).click();
  await page.getByRole('link', { name: 'Go/No-Go Assessment' }).click();
  const assessment = page.getByRole('article');
  await assessment.getByRole('combobox', { name: 'Assessment status' }).selectOption('PARTIAL');
  await assessment
    .getByLabel('Assessment rationale')
    .fill('Synthetic operator judgement; eligibility is not verified.');
  await assessment.getByRole('button', { name: 'Save Assessment' }).click();
  await expect(assessment.getByText('Current assessment: PARTIAL')).toBeVisible();
  await expect(
    assessment.getByText(
      'Current rationale: Synthetic operator judgement; eligibility is not verified.',
    ),
  ).toBeVisible();
  await page.getByRole('link', { name: 'Import snapshot', exact: true }).click();
  const bytes = Buffer.concat([
    Buffer.from(' \r\n'),
    readFileSync(new URL('../src/test/fixtures/tendercite-capability-v1.json', import.meta.url)),
    Buffer.from('\n\t'),
  ]);
  await page.getByLabel('Capability snapshot JSON').setInputFiles({
    name: 'synthetic-snapshot.json',
    mimeType: 'application/json',
    buffer: bytes,
  });
  for (const status of ['INSERTED', 'ALREADY_PRESENT']) {
    await page.getByRole('button', { name: 'Validate Snapshot', exact: true }).click();
    await expect(page.getByText('Validation passed — not imported')).toBeVisible();
    await page.getByRole('checkbox').check();
    const sent = page.waitForRequest((entry) =>
      entry.url().endsWith('/capability-snapshots/import'),
    );
    await page.getByRole('button', { name: 'Import Snapshot', exact: true }).click();
    expect((await sent).postDataBuffer()).toEqual(bytes);
    await expect(page.getByText(`Status: ${status}`, { exact: true })).toBeVisible();
  }
  expect(mutations).toHaveLength(9); // upload, intake validation/commit, review, assessment, 2×snapshot pairs
  expect(pageErrors).toEqual([]);
  await page.screenshot({
    path: test.info().outputPath('standalone-synthetic.png'),
    fullPage: true,
  });
});
