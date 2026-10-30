import { tenderDiscoveryPost } from './tendercite';
import { object, count, timestamp, uuid } from './capabilityMappingValidation';
import { tedPublication, tedNoticeUrl, dateOnly, warningsValid } from './tedReferences';
import { parseOpportunityRecord, type OpportunityRecord } from './opportunities';

export interface TedItem {
  publication_id: string;
  source: 'TED';
  title: string | null;
  organization: string | null;
  summary: string | null;
  publication_date: string | null;
  submission_deadline: string | null;
  source_url: string;
  retrieved_at: string;
  importable: boolean;
  warnings: string[];
}
export interface TedSearchResult {
  schema_version: 'ted-search-result-1';
  source: 'TED';
  scope: 'ACTIVE_COMPETITION';
  keyword: string;
  page: number;
  limit: number;
  total_notice_count: number;
  items: TedItem[];
  next_page: number | null;
  pagination_capped: boolean;
}
export class DiscoveryError extends Error {}
const codes = new Set([
  'DISCOVERY_API_DISABLED',
  'INVALID_DISCOVERY_INPUT',
  'DISCOVERY_REQUEST_TOO_LARGE',
  'DISCOVERY_OPERATION_FAILED',
  'TED_TIMEOUT',
  'TED_UNAVAILABLE',
  'TED_INVALID_RESPONSE',
  'TED_RATE_LIMITED',
  'TED_NOTICE_NOT_FOUND',
  'TED_NOTICE_NOT_IMPORTABLE',
  'OPPORTUNITY_ID_CONFLICT',
  'OPPORTUNITY_STORAGE_INTEGRITY',
  'DATABASE_FAILED',
  'UNSUPPORTED_MEDIA_TYPE',
  'UNSUPPORTED_CONTENT_ENCODING',
  'INVALID_CONTENT_LENGTH',
  'INCOMPLETE_BODY',
]);
async function responseError(response: Response): Promise<Error> {
  let code = '';
  try {
    const body = await response.json();
    if (typeof body?.detail?.code === 'string' && codes.has(body.detail.code))
      code = body.detail.code;
  } catch {
    /* Never show raw provider/proxy content. */
  }
  return new DiscoveryError(
    `TED discovery request failed (HTTP ${response.status})${code ? `: ${code}` : ''}. No automatic retry was made.`,
  );
}
const nullableText = (v: unknown, max: number): v is string | null =>
  v === null || (typeof v === 'string' && /\S/u.test(v) && [...v].length <= max);
function itemValid(v: unknown): v is TedItem {
  return (
    object(v) &&
    v.source === 'TED' &&
    tedPublication(v.publication_id) &&
    nullableText(v.title, 200) &&
    nullableText(v.organization, 200) &&
    nullableText(v.summary, 2000) &&
    dateOnly(v.publication_date) &&
    dateOnly(v.submission_deadline) &&
    tedNoticeUrl(v.source_url, v.publication_id) &&
    timestamp(v.retrieved_at) &&
    v.importable === !!v.title &&
    warningsValid(v.warnings)
  );
}
async function post(
  operation: 'search' | 'import',
  input: object,
  signal?: AbortSignal,
): Promise<unknown> {
  try {
    return await tenderDiscoveryPost(operation, JSON.stringify(input), signal, responseError);
  } catch (error) {
    if (signal?.aborted || error instanceof DiscoveryError) throw error;
    throw new DiscoveryError(
      'TED discovery response unavailable or invalid. No automatic retry was made.',
    );
  }
}
export async function searchTed(
  keyword: string,
  page: number,
  limit: number,
  signal: AbortSignal,
): Promise<TedSearchResult> {
  keyword = keyword.trim().replace(/\s+/gu, ' ');
  if (
    !keyword ||
    [...keyword].length > 150 ||
    !/^[\p{L}\p{N}_ .+-]+$/u.test(keyword) ||
    !count(page) ||
    page < 1 ||
    page > 50 ||
    !count(limit) ||
    limit < 1 ||
    limit > 20
  )
    throw new DiscoveryError(
      'Enter a literal keyword phrase (1–150 characters), a page from 1–50 and a limit from 1–20. Advanced query syntax is not supported.',
    );
  const v = await post(
    'search',
    { schema_version: 'ted-search-input-1', keyword, page, limit },
    signal,
  );
  if (
    !object(v) ||
    v.schema_version !== 'ted-search-result-1' ||
    v.source !== 'TED' ||
    v.scope !== 'ACTIVE_COMPETITION' ||
    v.keyword !== keyword ||
    v.page !== page ||
    v.limit !== limit ||
    !count(v.total_notice_count) ||
    !Array.isArray(v.items) ||
    v.items.length > limit ||
    v.total_notice_count < v.items.length ||
    !v.items.every(itemValid) ||
    new Set(v.items.map((i) => i.publication_id)).size !== v.items.length ||
    !(v.next_page === null || (page < 50 && v.next_page === page + 1)) ||
    v.pagination_capped !== v.total_notice_count > limit * 50
  )
    throw new DiscoveryError('Invalid or mismatched TED search response.');
  return {
    schema_version: v.schema_version,
    source: v.source,
    scope: v.scope,
    keyword,
    page,
    limit,
    total_notice_count: v.total_notice_count,
    next_page: v.next_page as number | null,
    pagination_capped: v.pagination_capped,
    items: v.items.map((i) => ({
      publication_id: i.publication_id,
      source: i.source,
      title: i.title,
      organization: i.organization,
      summary: i.summary,
      publication_date: i.publication_date,
      submission_deadline: i.submission_deadline,
      source_url: i.source_url,
      retrieved_at: i.retrieved_at,
      importable: i.importable,
      warnings: [...i.warnings],
    })),
  };
}
export async function importTed(
  id: string,
): Promise<{ status: 'CREATED' | 'ALREADY_PRESENT'; record: OpportunityRecord }> {
  if (!tedPublication(id)) throw new DiscoveryError('Select an existing TED publication.');
  const v = await post('import', { schema_version: 'ted-import-input-1', publication_id: id });
  if (
    !object(v) ||
    v.schema_version !== 'native-opportunity-save-result-1' ||
    !['CREATED', 'ALREADY_PRESENT'].includes(v.status as string) ||
    !object(v.record) ||
    !uuid(v.record.opportunity_id)
  )
    throw new DiscoveryError('Invalid TED import response; persistence is uncertain.');
  let record: OpportunityRecord;
  try {
    record = parseOpportunityRecord(v.record, v.record.opportunity_id);
  } catch {
    throw new DiscoveryError('Invalid TED import response; persistence is uncertain.');
  }
  if (
    record.opportunity.source_origin !== 'ted_import' ||
    record.opportunity.external_source?.external_id !== id ||
    (v.status === 'CREATED' &&
      (record.revision !== 1 || record.opportunity.required_skills.length !== 0))
  )
    throw new DiscoveryError('Mismatched TED import response; persistence is uncertain.');
  return { status: v.status as 'CREATED' | 'ALREADY_PRESENT', record };
}
