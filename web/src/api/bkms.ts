import { tenderBkmsRequest } from './tendercite';
import { object, count } from './capabilityMappingValidation';
import { parseOpportunityRecord, type OpportunityRecord } from './opportunities';
import { bkmsId, bkmsSourceValid, copyBkmsSource, digest, type BkmsSource } from './bkmsReferences';
import { dateOnly } from './tedReferences';
import { timestamp } from './capabilityMappingValidation';

export class BkmsError extends Error {
  constructor(
    message: string,
    readonly code = '',
  ) {
    super(message);
  }
}
const codes = new Set([
  'BKMS_DISCOVERY_API_DISABLED',
  'INVALID_BKMS_INPUT',
  'BKMS_REQUEST_TOO_LARGE',
  'BKMS_OPERATION_FAILED',
  'BKMS_CATALOG_NOT_FOUND',
  'BKMS_CATALOG_CONFLICT',
  'BKMS_STORAGE_INTEGRITY',
  'BKMS_NOTICE_NOT_FOUND',
  'BKMS_NOT_IMPORTABLE',
  'BKMS_INVALID_PUBLICATION_DAY',
  'BKMS_EXPORT_TOO_LARGE',
  'BKMS_BUSY',
  'BKMS_TIMEOUT',
  'BKMS_RATE_LIMITED',
  'BKMS_UNAVAILABLE',
  'BKMS_UNSUPPORTED_LICENSE',
  'BKMS_INVALID_EXPORT',
  'DATABASE_FAILED',
  'OPPORTUNITY_ID_CONFLICT',
  'OPPORTUNITY_STORAGE_INTEGRITY',
]);
async function responseError(response: Response): Promise<Error> {
  let code = '';
  try {
    const body = await response.json();
    if (codes.has(body?.detail?.code)) code = body.detail.code;
  } catch {
    /* Never render provider bodies. */
  }
  return new BkmsError(
    `German procurement request failed (HTTP ${response.status})${code ? ': ' + code : ''}.`,
    code,
  );
}
async function request(
  operation: 'catalog' | 'load' | 'search' | 'import',
  body?: object,
  signal?: AbortSignal,
): Promise<unknown> {
  try {
    return await tenderBkmsRequest(
      operation,
      body ? JSON.stringify(body) : undefined,
      signal,
      responseError,
    );
  } catch (error) {
    if (signal?.aborted || error instanceof BkmsError) throw error;
    throw new BkmsError(
      'German procurement response unavailable or invalid. No automatic retry was made.',
    );
  }
}
export interface BkmsCatalog {
  schema_version: 'bkms-catalog-summary-1';
  catalog_id: string;
  export_day: string;
  retrieved_at: string;
  archive_sha256: string;
  record_count: number;
  importable_count: number;
  warning_count: number;
}
function catalog(value: unknown): BkmsCatalog {
  if (
    !object(value) ||
    value.schema_version !== 'bkms-catalog-summary-1' ||
    !digest(value.catalog_id) ||
    !digest(value.archive_sha256) ||
    typeof value.export_day !== 'string' ||
    !dateOnly(value.export_day) ||
    !timestamp(value.retrieved_at) ||
    !count(value.record_count) ||
    value.record_count > 4000 ||
    !count(value.importable_count) ||
    value.importable_count > value.record_count ||
    !count(value.warning_count) ||
    value.warning_count > value.record_count
  )
    throw new BkmsError('Invalid daily catalog response.');
  return {
    schema_version: value.schema_version,
    catalog_id: value.catalog_id,
    export_day: value.export_day,
    retrieved_at: value.retrieved_at,
    archive_sha256: value.archive_sha256,
    record_count: value.record_count,
    importable_count: value.importable_count,
    warning_count: value.warning_count,
  };
}
export async function currentBkmsCatalog(signal?: AbortSignal): Promise<BkmsCatalog | null> {
  try {
    return catalog(await request('catalog', undefined, signal));
  } catch (error) {
    if (error instanceof BkmsError && error.code === 'BKMS_CATALOG_NOT_FOUND') return null;
    throw error;
  }
}
export async function loadBkmsDay(day: string): Promise<BkmsCatalog> {
  if (!day || !dateOnly(day)) throw new BkmsError('Select one completed publication day.');
  const value = catalog(
    await request('load', { schema_version: 'bkms-load-input-1', export_day: day }),
  );
  if (value.export_day !== day)
    throw new BkmsError('Mismatched daily catalog response; load outcome uncertain.');
  return value;
}
export interface BkmsResults {
  schema_version: 'bkms-search-result-1';
  catalog: BkmsCatalog;
  keyword: string;
  page: number;
  limit: number;
  total_count: number;
  items: BkmsSource[];
  next_page: number | null;
}
export async function searchBkms(
  id: string,
  keyword: string,
  page: number,
  limit: number,
  signal?: AbortSignal,
): Promise<BkmsResults> {
  keyword = keyword.trim().replace(/\s+/gu, ' ');
  if (
    !digest(id) ||
    !keyword ||
    [...keyword].length > 150 ||
    !/^[\p{L}\p{N}_ .+-]+$/u.test(keyword) ||
    !count(page) ||
    page < 1 ||
    page > 4000 ||
    !count(limit) ||
    limit < 1 ||
    limit > 20
  )
    throw new BkmsError(
      'Enter a literal keyword phrase (1–150 characters) and select a loaded daily catalog.',
    );
  const v = await request(
    'search',
    { schema_version: 'bkms-search-input-1', catalog_id: id, keyword, page, limit },
    signal,
  );
  if (!object(v)) throw new BkmsError('Invalid local search response.');
  const c = catalog(v.catalog);
  if (
    v.schema_version !== 'bkms-search-result-1' ||
    c.catalog_id !== id ||
    v.keyword !== keyword ||
    v.page !== page ||
    v.limit !== limit ||
    !count(v.total_count) ||
    v.total_count > c.record_count ||
    !Array.isArray(v.items) ||
    v.items.length > limit ||
    v.items.length > v.total_count ||
    !v.items.every(bkmsSourceValid) ||
    v.items.some(
      (i) =>
        i.archive_sha256 !== c.archive_sha256 ||
        i.export_day !== c.export_day ||
        i.retrieved_at !== c.retrieved_at,
    ) ||
    new Set(v.items.map((i) => i.external_id)).size !== v.items.length ||
    v.next_page !== (page * limit < v.total_count ? page + 1 : null)
  )
    throw new BkmsError('Invalid or mismatched local search response.');
  return {
    schema_version: v.schema_version,
    catalog: c,
    keyword,
    page,
    limit,
    total_count: v.total_count,
    items: v.items.map(copyBkmsSource),
    next_page: v.next_page as number | null,
  };
}
export async function importBkms(
  catalogId: string,
  id: string,
): Promise<{ status: 'CREATED' | 'ALREADY_PRESENT'; record: OpportunityRecord }> {
  if (!digest(catalogId) || !bkmsId(id))
    throw new BkmsError('Select a publication from the loaded catalog.');
  const v = await request('import', {
    schema_version: 'bkms-import-input-1',
    catalog_id: catalogId,
    external_id: id,
  });
  if (
    !object(v) ||
    v.schema_version !== 'native-opportunity-save-result-1' ||
    !['CREATED', 'ALREADY_PRESENT'].includes(v.status as string) ||
    !object(v.record)
  )
    throw new BkmsError('Invalid import response; persistence is uncertain.');
  const record = parseOpportunityRecord(v.record, v.record.opportunity_id as string);
  if (
    record.opportunity.source_origin !== 'bkms_import' ||
    record.opportunity.external_source?.external_id !== id ||
    (v.status === 'CREATED' &&
      (record.revision !== 1 || record.opportunity.required_skills.length !== 0))
  )
    throw new BkmsError('Mismatched import response; persistence is uncertain.');
  return { status: v.status as 'CREATED' | 'ALREADY_PRESENT', record };
}
