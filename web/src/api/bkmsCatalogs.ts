import { object, count } from './capabilityMappingValidation';
import {
  bkmsSourceValid,
  copyBkmsSource,
  digest,
  bkmsWarnings,
  type BkmsSource,
} from './bkmsReferences';
import { BkmsError, bkmsRequest, parseBkmsCatalog, type BkmsCatalog } from './bkms';
import { dateOnly } from './tedReferences';
import { parseDeadlineEvidence, type DeadlineEvidence } from './bkmsDeadlines';

export const noticeFilters = ['ALL', 'TENDER', 'AWARD', 'PLANNING', 'OTHER_UNKNOWN'] as const;
export type NoticeFilter = (typeof noticeFilters)[number];
export interface StoredDay {
  catalog: BkmsCatalog;
  warning_counts: Record<string, number>;
}
export interface BkmsCollection {
  schema_version: 'bkms-catalog-collection-1';
  collection_id: string;
  days: StoredDay[];
  stored_bytes: number;
  max_days: 7;
  max_storage_bytes: 100663296;
}
export function parseCollection(v: unknown): BkmsCollection {
  if (
    !object(v) ||
    v.schema_version !== 'bkms-catalog-collection-1' ||
    !digest(v.collection_id) ||
    v.max_days !== 7 ||
    v.max_storage_bytes !== 100663296 ||
    !count(v.stored_bytes) ||
    v.stored_bytes > v.max_storage_bytes ||
    !Array.isArray(v.days) ||
    v.days.length > 7
  )
    throw new BkmsError('Invalid local catalog collection response.');
  const days: StoredDay[] = v.days.map((entry: unknown) => {
    if (!object(entry) || !object(entry.warning_counts))
      throw new BkmsError('Invalid stored-day metadata.');
    const catalog = parseBkmsCatalog(entry.catalog);
    const warnings = Object.entries(entry.warning_counts);
    if (
      warnings.length > 9 ||
      warnings.some(
        ([key, value]) =>
          !bkmsWarnings.includes(key as (typeof bkmsWarnings)[number]) ||
          !count(value) ||
          value < 1 ||
          value > catalog.record_count,
      )
    )
      throw new BkmsError('Invalid source warning counts.');
    return { catalog, warning_counts: Object.fromEntries(warnings) as Record<string, number> };
  });
  if (
    new Set(days.map((d) => d.catalog.export_day)).size !== days.length ||
    days.some((d, i) => i > 0 && days[i - 1]!.catalog.export_day <= d.catalog.export_day)
  )
    throw new BkmsError('Invalid stored-day ordering.');
  return {
    schema_version: v.schema_version,
    collection_id: v.collection_id,
    days,
    stored_bytes: v.stored_bytes,
    max_days: 7,
    max_storage_bytes: 100663296,
  };
}
export async function getBkmsCollection(signal?: AbortSignal): Promise<BkmsCollection> {
  return parseCollection(await bkmsRequest('catalogs', undefined, signal));
}
export async function removeBkmsDay(day: string, expected: string): Promise<BkmsCollection> {
  if (!day || !dateOnly(day) || !digest(expected))
    throw new BkmsError('Select a current stored day to remove.');
  const result = parseCollection(
    await bkmsRequest('remove', {
      schema_version: 'bkms-remove-input-1',
      export_day: day,
      expected_catalog_id: expected,
    }),
  );
  if (result.days.some((d) => d.catalog.export_day === day))
    throw new BkmsError('Removal response did not confirm removal.');
  return result;
}
export interface NoticeOccurrence {
  export_day: string;
  catalog_id: string;
  record_sha256: string;
}
export interface BkmsHit {
  source: BkmsSource;
  deadline_enrichment?: DeadlineEvidence | null;
  notice_category: Exclude<NoticeFilter, 'ALL'>;
  version_count: number;
  is_latest_stored: boolean;
  occurrences: NoticeOccurrence[];
  source_record_hashes_differ: boolean;
}
export interface MultiBkmsResults {
  schema_version: 'bkms-search-result-2';
  coverage: BkmsCollection;
  keyword: string;
  page: number;
  limit: number;
  notice_category: NoticeFilter;
  include_earlier_versions: boolean;
  total_count: number;
  items: BkmsHit[];
  next_page: number | null;
}
function parseHit(v: unknown, coverage: BkmsCollection): BkmsHit {
  if (
    !object(v) ||
    !bkmsSourceValid(v.source) ||
    !noticeFilters.slice(1).includes(v.notice_category as Exclude<NoticeFilter, 'ALL'>) ||
    !count(v.version_count) ||
    v.version_count < 1 ||
    v.version_count > 28000 ||
    typeof v.is_latest_stored !== 'boolean' ||
    !Array.isArray(v.occurrences) ||
    v.occurrences.length < 1 ||
    v.occurrences.length > 7 ||
    typeof v.source_record_hashes_differ !== 'boolean'
  )
    throw new BkmsError('Invalid notice version metadata.');
  const source = v.source;
  const occurrences: NoticeOccurrence[] = v.occurrences.map((o: unknown) => {
    if (
      !object(o) ||
      !digest(o.catalog_id) ||
      !digest(o.record_sha256) ||
      typeof o.export_day !== 'string' ||
      !coverage.days.some(
        (d) => d.catalog.export_day === o.export_day && d.catalog.catalog_id === o.catalog_id,
      )
    )
      throw new BkmsError('Invalid notice occurrence.');
    return { export_day: o.export_day, catalog_id: o.catalog_id, record_sha256: o.record_sha256 };
  });
  const first = occurrences[0]!;
  const day = coverage.days.find((d) => d.catalog.export_day === source.export_day)?.catalog;
  if (
    !day ||
    source.export_day !== first.export_day ||
    source.record_sha256 !== first.record_sha256 ||
    source.archive_sha256 !== day.archive_sha256 ||
    source.retrieved_at !== day.retrieved_at ||
    new Set(occurrences.map((o) => o.export_day)).size !== occurrences.length ||
    occurrences.some((o, i) => i > 0 && occurrences[i - 1]!.export_day <= o.export_day) ||
    new Set(occurrences.map((o) => o.record_sha256)).size > 1 !== v.source_record_hashes_differ
  )
    throw new BkmsError('Mismatched notice provenance.');
  return {
    source: copyBkmsSource(source),
    ...(v.deadline_enrichment === undefined
      ? {}
      : {
          deadline_enrichment:
            v.deadline_enrichment === null
              ? null
              : parseDeadlineEvidence(v.deadline_enrichment, source, first.catalog_id),
        }),
    notice_category: v.notice_category as Exclude<NoticeFilter, 'ALL'>,
    version_count: v.version_count,
    is_latest_stored: v.is_latest_stored,
    occurrences,
    source_record_hashes_differ: v.source_record_hashes_differ,
  };
}
export async function searchAllBkms(
  collectionId: string,
  keyword: string,
  page: number,
  limit: number,
  category: NoticeFilter,
  earlier: boolean,
): Promise<MultiBkmsResults> {
  keyword = keyword.trim().replace(/\s+/gu, ' ');
  if (
    !digest(collectionId) ||
    !keyword ||
    [...keyword].length > 150 ||
    !/^[\p{L}\p{N}_ .+-]+$/u.test(keyword) ||
    !count(page) ||
    page < 1 ||
    page > 28000 ||
    !count(limit) ||
    limit < 1 ||
    limit > 20 ||
    !noticeFilters.includes(category) ||
    typeof earlier !== 'boolean'
  )
    throw new BkmsError('Enter a literal keyword and select supported local filters.');
  const v = await bkmsRequest('search', {
    schema_version: 'bkms-search-input-2',
    collection_id: collectionId,
    keyword,
    page,
    limit,
    notice_category: category,
    include_earlier_versions: earlier,
  });
  if (!object(v)) throw new BkmsError('Invalid combined search response.');
  const coverage = parseCollection(v.coverage);
  if (
    v.schema_version !== 'bkms-search-result-2' ||
    coverage.collection_id !== collectionId ||
    v.keyword !== keyword ||
    v.page !== page ||
    v.limit !== limit ||
    v.notice_category !== category ||
    v.include_earlier_versions !== earlier ||
    !count(v.total_count) ||
    v.total_count > coverage.days.reduce((n, d) => n + d.catalog.record_count, 0) ||
    !Array.isArray(v.items) ||
    v.items.length > limit ||
    v.items.length > v.total_count ||
    v.next_page !== (page * limit < v.total_count ? page + 1 : null)
  )
    throw new BkmsError('Invalid or mismatched combined search response.');
  const items = v.items.map((i: unknown) => parseHit(i, coverage));
  if (
    new Set(items.map((i) => i.source.external_id)).size !== items.length ||
    items.some(
      (i) =>
        (category !== 'ALL' && i.notice_category !== category) || (!earlier && !i.is_latest_stored),
    )
  )
    throw new BkmsError('Mismatched notice selection.');
  return {
    schema_version: v.schema_version,
    coverage,
    keyword,
    page,
    limit,
    notice_category: category,
    include_earlier_versions: earlier,
    total_count: v.total_count,
    items,
    next_page: v.next_page as number | null,
  };
}
