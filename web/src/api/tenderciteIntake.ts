import { tenderGet, tenderIntakePost, type TenderDocument } from './tendercite';

// TenderCite's existing RequirementCategory / RequirementType enums, not inferred labels.
export const categories = ['MUST', 'SCORING', 'INFORMATION', 'RISK'] as const;
export const requirementTypes = [
  'DEADLINE',
  'ELIGIBILITY',
  'REFERENCE',
  'FINANCIAL',
  'INSURANCE',
  'TECHNICAL',
  'CERTIFICATE',
  'EVIDENCE',
  'AWARD',
  'PRICE',
  'CONTRACT',
  'LIABILITY',
  'PRIVACY',
  'SECURITY',
  'OTHER',
] as const;
export type SourceDocument = Pick<TenderDocument, 'id' | 'filename' | 'sha256' | 'page_count'>;
export interface CitationDraft {
  document_id: string;
  page_number: string;
  quote: string;
}
export interface RequirementDraft {
  item_id: string;
  statement: string;
  category: (typeof categories)[number] | '';
  requirement_type: (typeof requirementTypes)[number] | '';
  evidence: CitationDraft[];
  interpretation_notes: string;
}
export interface CuratedTenderIntake {
  schema_version: 'curated-tender-intake-1';
  source_origin: 'manual_operator_entry';
  purpose: string;
  documents: { document_id: string; expected_pdf_sha256: string }[];
  findings: {
    item_id: string;
    statement: string;
    category: (typeof categories)[number];
    requirement_type: (typeof requirementTypes)[number];
    evidence: { document_id: string; page_number: number; quote: string }[];
    interpretation_notes?: string;
  }[];
}
export interface IntakeSummary {
  schema_version: 'curated-tender-intake-result-1';
  status: 'VALIDATED' | 'CREATED' | 'ALREADY_PRESENT';
  analysis_id: string;
  intake_content_hash: string;
  source_origin: 'manual_operator_entry';
  intake_schema_version: 'curated-tender-intake-1';
  source_count: number;
  finding_count: number;
  quotation_count: number;
}
export class IntakeInputError extends Error {}
export const MAX_INTAKE_BYTES = 1024 * 1024;
const identifier = /^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$/;
function isSource(value: unknown): value is SourceDocument {
  if (!value || typeof value !== 'object') return false;
  return (
    'id' in value &&
    typeof value.id === 'string' &&
    identifier.test(value.id) &&
    'sha256' in value &&
    typeof value.sha256 === 'string' &&
    /^[a-f0-9]{64}$/.test(value.sha256) &&
    'filename' in value &&
    typeof value.filename === 'string' &&
    'page_count' in value &&
    typeof value.page_count === 'number' &&
    Number.isSafeInteger(value.page_count) &&
    value.page_count > 0
  );
}
export async function intakeDocuments(signal: AbortSignal): Promise<SourceDocument[]> {
  const data = await tenderGet<unknown>('documents', signal);
  if (
    !Array.isArray(data) ||
    !data.every(isSource) ||
    new Set(data.map((doc) => doc.id)).size !== data.length
  )
    throw new Error(
      'Invalid TenderCite document catalogue. Refresh before preparing requirements.',
    );
  return data;
}
function boundedText(value: string, max: number, label: string) {
  if (!value.trim() || value.length > max)
    throw new IntakeInputError(`${label} is required and must be at most ${max} characters.`);
  return value; // Check without trimming or repairing operator/source text.
}
export function intakeBody(intake: CuratedTenderIntake, operation: 'validate' | 'commit') {
  const body = JSON.stringify(
    operation === 'validate' ? intake : { intake, confirm_manual_intake: true },
  );
  if (new TextEncoder().encode(body).byteLength > MAX_INTAKE_BYTES)
    throw new IntakeInputError(
      'The complete request exceeds the 1 MiB limit. Reduce the intake before validating.',
    );
  return body;
}
export function buildIntake(
  purpose: string,
  drafts: RequirementDraft[],
  sources: SourceDocument[],
): CuratedTenderIntake {
  if (!drafts.length || drafts.length > 100)
    throw new IntakeInputError('Select between 1 and 100 requirements.');
  const inventory = new Map<string, SourceDocument>();
  const itemIds = new Set<string>();
  const findings = drafts.map((draft) => {
    if (!identifier.test(draft.item_id) || itemIds.has(draft.item_id))
      throw new IntakeInputError('Invalid or duplicate local requirement identity.');
    itemIds.add(draft.item_id);
    if (
      !draft.category ||
      !categories.includes(draft.category) ||
      !draft.requirement_type ||
      !requirementTypes.includes(draft.requirement_type)
    )
      throw new IntakeInputError('Select a category and requirement type for every requirement.');
    if (!draft.evidence.length || draft.evidence.length > 20)
      throw new IntakeInputError('Each requirement needs supporting evidence.');
    const evidence = draft.evidence.map((citation) => {
      const document = sources.find((source) => source.id === citation.document_id);
      if (!document || !isSource(document))
        throw new IntakeInputError('Select an existing source document for every citation.');
      const page = Number(citation.page_number);
      if (!Number.isSafeInteger(page) || page < 1 || page > document.page_count)
        throw new IntakeInputError('Select a valid physical PDF page within the source document.');
      inventory.set(document.id, document);
      return {
        document_id: document.id,
        page_number: page,
        quote: boundedText(citation.quote, 4000, 'Supporting quotation'),
      };
    });
    return {
      item_id: draft.item_id,
      statement: boundedText(draft.statement, 4000, 'Requirement statement'),
      category: draft.category,
      requirement_type: draft.requirement_type,
      evidence,
      ...(draft.interpretation_notes.trim()
        ? {
            interpretation_notes: boundedText(
              draft.interpretation_notes,
              1000,
              'Interpretation note',
            ),
          }
        : {}),
    };
  });
  if (inventory.size > 100) throw new IntakeInputError('Select at most 100 source documents.');
  const result: CuratedTenderIntake = {
    schema_version: 'curated-tender-intake-1',
    source_origin: 'manual_operator_entry',
    purpose: boundedText(purpose, 2000, 'Intake purpose'),
    findings,
    documents: [...inventory.values()]
      .sort((a, b) => a.id.localeCompare(b.id))
      .map((doc) => ({ document_id: doc.id, expected_pdf_sha256: doc.sha256 })),
  };
  intakeBody(result, 'commit'); // Validation should not create a snapshot too large to commit.
  return result;
}

const safeCodes = new Set([
  'INVALID_CONTENT_LENGTH',
  'SOURCE_DOCUMENT_NOT_FOUND',
  'SOURCE_PAGE_NOT_FOUND',
  'SOURCE_DOCUMENT_NOT_PDF',
  'SOURCE_PDF_HASH_MISMATCH',
  'SOURCE_PAGES_INCOMPLETE',
  'INTAKE_IDENTITY_CONFLICT',
  'INTAKE_SOURCE_STATE_CHANGED',
  'INTAKE_TOO_LARGE',
  'UNSUPPORTED_MEDIA_TYPE',
  'UNSUPPORTED_CONTENT_ENCODING',
  'INVALID_CURATED_INTAKE',
  'MANUAL_INTAKE_CONFIRMATION_REQUIRED',
  'SOURCE_QUOTE_NOT_VERIFIED',
  'INTAKE_STORAGE_INTEGRITY',
  'DATABASE_FAILED',
  'CURATED_INTAKE_FAILED',
]);
export class IntakeRequestError extends Error {}
async function responseError(response: Response): Promise<Error> {
  let code = '';
  try {
    const data = await response.json();
    if (typeof data?.detail?.code === 'string' && safeCodes.has(data.detail.code))
      code = `: ${data.detail.code}`;
  } catch {
    /* Never show a raw backend or proxy response. */
  }
  return new IntakeRequestError(`TenderCite request failed (HTTP ${response.status})${code}.`);
}
export async function sendIntake(
  operation: 'validate' | 'commit',
  intake: CuratedTenderIntake,
  validated?: IntakeSummary,
): Promise<IntakeSummary> {
  if (operation === 'commit' && !validated)
    throw new IntakeInputError('Validate the intake before saving.');
  const body = intakeBody(intake, operation);
  let result: unknown;
  try {
    result = await tenderIntakePost(operation, body, responseError);
  } catch (error) {
    if (error instanceof IntakeRequestError) throw error;
    throw new IntakeRequestError('TenderCite is unavailable or returned an invalid response.', {
      cause: error,
    });
  }
  if (
    !result ||
    typeof result !== 'object' ||
    !('schema_version' in result) ||
    result.schema_version !== 'curated-tender-intake-result-1' ||
    !('source_origin' in result) ||
    result.source_origin !== 'manual_operator_entry' ||
    !('intake_schema_version' in result) ||
    result.intake_schema_version !== intake.schema_version ||
    !('status' in result) ||
    (operation === 'validate'
      ? result.status !== 'VALIDATED'
      : result.status !== 'CREATED' && result.status !== 'ALREADY_PRESENT') ||
    !('analysis_id' in result) ||
    typeof result.analysis_id !== 'string' ||
    !/^curated-[a-f0-9]{64}$/.test(result.analysis_id) ||
    !('intake_content_hash' in result) ||
    result.intake_content_hash !== `sha256:${result.analysis_id.slice(8)}` ||
    !('source_count' in result) ||
    result.source_count !== intake.documents.length ||
    !('finding_count' in result) ||
    result.finding_count !== intake.findings.length ||
    !('quotation_count' in result) ||
    result.quotation_count !== intake.findings.reduce((n, f) => n + f.evidence.length, 0) ||
    (validated &&
      (result.analysis_id !== validated.analysis_id ||
        result.intake_content_hash !== validated.intake_content_hash))
  ) {
    throw new IntakeRequestError('TenderCite returned an invalid intake summary.');
  }
  return result as IntakeSummary;
}
