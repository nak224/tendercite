import { readFileSync } from 'node:fs';
import { test, expect } from '@playwright/test';
import { syntheticPdf } from '../src/test/tenderPdf';

// Opt-in only. Start the backend with a NEW disposable data directory, retrieval disabled,
// no provider configuration and snapshot/mapping/report flags enabled. No TCE service is used.
test('standalone PDF → intake → human review → assessment → snapshot → mapping → capability report', async ({
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
  const reportRequests: string[] = [];
  const pageErrors: string[] = [];
  page.on('pageerror', (error) => pageErrors.push(error.message));
  page.on('request', (sent) => {
    expect(new URL(sent.url()).origin).toBe(baseURL);
    if (sent.method() !== 'GET') {
      if (new URL(sent.url()).pathname.endsWith('/capability-workflow/report')) {
        reportRequests.push(sent.url());
        return;
      }
      expect(sent.url()).toMatch(
        /\/api\/v1\/(documents|curated-intakes|findings|go-no-go|capability-snapshots|capability-mappings)/,
      );
      mutations.push(new URL(sent.url()).pathname);
    }
  });
  await page.goto('/tenders');
  await expect(page.getByText('No documents available.', { exact: true })).toBeVisible();
  const file = syntheticPdf(
    'standalone-synthetic.pdf',
    'application/pdf',
    'PostgreSQL is required.',
  );
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
  await requirement.getByLabel('Requirement statement').fill('The tender requires PostgreSQL.');
  await requirement.getByRole('combobox', { name: 'Category', exact: true }).selectOption('MUST');
  await requirement
    .getByRole('combobox', { name: 'Requirement type', exact: true })
    .selectOption('TECHNICAL');
  await requirement.getByRole('combobox', { name: 'Source document' }).selectOption(document.id);
  await requirement.getByLabel('Physical PDF page').fill('1');
  await requirement.getByLabel('Exact supporting quotation').fill('PostgreSQL is required.');
  await page.getByRole('button', { name: 'Validate', exact: true }).click();
  await expect(page.getByRole('region', { name: 'Validated intake' })).toBeVisible();
  await page.getByRole('checkbox').check();
  await page.getByRole('button', { name: 'Save requirements' }).click();
  await expect(page.getByRole('region', { name: 'Saved intake' })).toContainText('UNREVIEWED');
  await page.getByRole('link', { name: 'Open analysis', exact: true }).click();
  const analysisId = new URL(page.url()).pathname.split('/').at(-1)!;
  await page.getByRole('link', { name: 'The tender requires PostgreSQL.', exact: true }).click();
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
  await expect(page.locator('pre.source-page-text')).toHaveText('PostgreSQL is required.');
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
  const snapshotHash = JSON.parse(bytes.toString('utf8')).content_hash;
  const findingId = new URL(findingUrl).pathname.split('/').at(-1)!;
  async function openMapping() {
    await page.getByRole('link', { name: 'Skill Mapping', exact: true }).click();
    await page.getByRole('combobox', { name: 'Analysis', exact: true }).selectOption(analysisId);
    await page.getByRole('combobox', { name: 'Imported snapshot' }).selectOption(snapshotHash);
    await page.getByRole('button', { name: 'Load mapping overview' }).click();
    await expect(page.getByRole('region', { name: 'Mapping overview' })).toBeVisible();
    await page.getByRole('combobox', { name: 'Requirement', exact: true }).selectOption(findingId);
  }
  await openMapping();
  let editor = page.getByRole('region', { name: 'Selected mapping' });
  await editor.getByRole('checkbox', { name: /PostgreSQL/ }).check();
  await editor.getByLabel('Mapping rationale').fill('Manual synthetic PostgreSQL association.');
  await editor.getByLabel('Non-personal operator alias (optional)').fill('synthetic_operator');
  await editor.getByRole('button', { name: 'Review proposed mapping' }).click();
  const drafted = page.waitForResponse((r) => r.url().endsWith('/capability-mappings/drafts'));
  await page.getByRole('button', { name: 'Create DRAFT' }).click();
  const draft = await (await drafted).json();
  await expect(editor.getByText('Mapping state: DRAFT', { exact: true })).toBeVisible();
  await expect(editor.getByRole('button', { name: 'APPROVE mapping' })).toBeDisabled();
  await editor.getByLabel('Approval rationale').fill('Explicit synthetic approval simulation.');
  await editor.getByLabel('Non-personal operator alias (optional)').fill('synthetic_operator');
  await editor.getByRole('checkbox').check();
  const approval = page.waitForRequest((r) =>
    r.url().endsWith(`/capability-mappings/${draft.mapping_id}/approve`),
  );
  await editor.getByRole('button', { name: 'APPROVE mapping' }).click();
  expect((await approval).postDataJSON()).toEqual({
    schema_version: 'capability-mapping-approval-1',
    mapping_id: draft.mapping_id,
    expected_revision: draft.revision,
    expected_review_event_id: draft.review_binding.review_event_id,
    expected_review_digest: draft.review_binding.digest,
    rationale: 'Explicit synthetic approval simulation.',
    operator_alias: 'synthetic_operator',
  });
  await expect(editor.getByText('Mapping state: APPROVED', { exact: true })).toBeVisible();
  await page.screenshot({
    path: test.info().outputPath('approved-synthetic-mapping.png'),
    fullPage: true,
  });
  async function runMatching() {
    await page.getByRole('link', { name: 'Capability Matching', exact: true }).click();
    // Both pages have scope selectors; wait for the new route before selecting them.
    await expect(
      page.getByRole('heading', { name: 'Capability Matching', exact: true }),
    ).toBeVisible();
    await page.getByRole('combobox', { name: 'Analysis', exact: true }).selectOption(analysisId);
    await page.getByRole('combobox', { name: 'Imported snapshot' }).selectOption(snapshotHash);
    await page.getByRole('checkbox', { name: /The tender requires PostgreSQL/ }).check();
    const response = page.waitForResponse((r) => r.url().endsWith('/capability-workflow/report'));
    await page.getByRole('button', { name: 'Run Capability Matching' }).click();
    const received = await response;
    expect(received.request().postDataJSON()).toEqual({
      schema_version: 'capability-workflow-assessment-selection-1',
      analysis_id: analysisId,
      snapshot_hash: snapshotHash,
      finding_ids: [findingId],
    });
    expect(received.status()).toBe(200);
    return page.getByRole('article', { name: `Requirement ${findingId}` });
  }
  let matched = await runMatching();
  await expect(matched.getByText('Recorded coverage: ALL_REQUIRED_SKILLS_RECORDED')).toBeVisible();
  await expect(matched.getByText(/All required skills recorded: 2/)).toBeVisible();
  await matched.getByText('Individual recorded-skill coverage (3)').click();
  await expect(matched.getByText(/Pseudonymous person ID:/)).toHaveCount(3);
  await page.screenshot({
    path: test.info().outputPath('synthetic-capability-report.png'),
    fullPage: true,
  });
  await matched.getByRole('link', { name: 'Finding detail, review and source evidence' }).click();
  const laterReview = page.getByRole('region', { name: 'Human finding review' });
  await laterReview.getByRole('combobox', { name: 'Review action' }).selectOption('CONFIRM');
  await laterReview
    .getByLabel('Review comment (optional)')
    .fill('New simulated review invalidates the old mapping approval.');
  await laterReview.getByRole('button', { name: 'Save Review' }).click();
  await expect(page.getByRole('region', { name: 'Review history' })).toContainText(
    'New simulated review invalidates',
  );
  await openMapping();
  editor = page.getByRole('region', { name: 'Selected mapping' });
  await expect(editor.getByText('Mapping state: STALE', { exact: true })).toBeVisible();
  await expect(editor.getByRole('button', { name: 'APPROVE mapping' })).toHaveCount(0);
  matched = await runMatching();
  await expect(matched.getByText('Mapping: STALE', { exact: true })).toBeVisible();
  await expect(
    matched.getByText('Recorded coverage: NOT_EVALUATED', { exact: true }),
  ).toBeVisible();
  await expect(matched.getByText(/Individual recorded-skill coverage/)).toHaveCount(0);
  expect(mutations).toHaveLength(12); // existing 9 + explicit draft, approval and a new simulated review
  expect(reportRequests).toHaveLength(2); // explicit read-only computations, never writes or retries
  expect(pageErrors).toEqual([]);
  await page.screenshot({
    path: test.info().outputPath('standalone-synthetic.png'),
    fullPage: true,
  });
});
