import { object, timestamp } from './capabilityMappingValidation';
import { validDate } from '../utils/period';

export const tedWarnings = [
  'MISSING_TITLE',
  'MISSING_BUYER',
  'MISSING_DESCRIPTION',
  'MISSING_PUBLICATION_DATE',
  'MISSING_DEADLINE',
  'MULTIPLE_TENDER_DEADLINES',
  'TITLE_SHORTENED',
  'ORGANIZATION_SHORTENED',
  'SUMMARY_SHORTENED',
] as const;
export const tedPublication = (v: unknown): v is string =>
  typeof v === 'string' && /^[1-9][0-9]{0,7}-[0-9]{4}$/.test(v);
export function tedNoticeUrl(v: unknown, id: string): v is string {
  if (typeof v !== 'string') return false;
  try {
    const url = new URL(v);
    return (
      url.protocol === 'https:' &&
      url.host === 'ted.europa.eu' &&
      !url.username &&
      !url.password &&
      !url.search &&
      !url.hash &&
      new RegExp(
        `^/(?:bg|cs|da|de|el|en|es|et|fi|fr|ga|hr|hu|it|lt|lv|mt|nl|pl|pt|ro|sk|sl|sv)/notice/-/detail/${id}$`,
      ).test(url.pathname)
    );
  } catch {
    return false;
  }
}
export const dateOnly = (v: unknown): v is string | null =>
  v === null || (typeof v === 'string' && !v.startsWith('0000-') && validDate(v));
export const warningsValid = (v: unknown): v is string[] =>
  Array.isArray(v) &&
  v.length <= 12 &&
  new Set(v).size === v.length &&
  v.every((w) => tedWarnings.includes(w));
export interface TedSource {
  schema_version: 'ted-opportunity-source-1';
  source: 'TED';
  external_id: string;
  original_url: string;
  publication_date: string | null;
  retrieved_at: string;
  mapping_warnings: string[];
}
export function tedSourceValid(v: unknown): v is TedSource {
  return (
    object(v) &&
    v.schema_version === 'ted-opportunity-source-1' &&
    v.source === 'TED' &&
    tedPublication(v.external_id) &&
    tedNoticeUrl(v.original_url, v.external_id) &&
    dateOnly(v.publication_date) &&
    timestamp(v.retrieved_at) &&
    warningsValid(v.mapping_warnings)
  );
}
export function copyTedSource(s: TedSource): TedSource {
  return {
    schema_version: s.schema_version,
    source: s.source,
    external_id: s.external_id,
    original_url: s.original_url,
    publication_date: s.publication_date,
    retrieved_at: s.retrieved_at,
    mapping_warnings: [...s.mapping_warnings],
  };
}
