import { object, timestamp, uuid } from './capabilityMappingValidation';
import { dateOnly } from './tedReferences';

export const bkmsWarnings = [
  'MISSING_TITLE',
  'MISSING_BUYER',
  'MISSING_DESCRIPTION',
  'MISSING_PUBLICATION_DATE',
  'MISSING_DEADLINE',
  'TITLE_SHORTENED',
  'ORGANIZATION_SHORTENED',
  'SUMMARY_SHORTENED',
  'SOURCE_METADATA_SHORTENED',
] as const;
export const digest = (v: unknown): v is string =>
  typeof v === 'string' && /^[0-9a-f]{64}$/.test(v);
export function bkmsId(v: unknown): v is string {
  if (typeof v !== 'string') return false;
  const split = v.lastIndexOf('-');
  return (
    /^[0-9]{1,10}$/.test(v.slice(split + 1)) &&
    (uuid(v.slice(0, split)) || /^[0-9]{1,20}$/.test(v.slice(0, split)))
  );
}
export function bkmsUrl(id: string): string {
  const split = id.lastIndexOf('-');
  return `https://oeffentlichevergabe.de/ui/notices/${id.slice(0, split)}?noticeVersion=${id.slice(split + 1)}`;
}
export interface BkmsSource {
  schema_version: 'bkms-opportunity-source-1';
  source: 'BEKANNTMACHUNGSSERVICE';
  external_id: string;
  original_url: string;
  record_url: string;
  ocid: string | null;
  export_day: string;
  retrieved_at: string;
  archive_sha256: string;
  record_sha256: string;
  license: 'https://opendefinition.org/licenses/cc-zero/';
  publication_policy: 'https://oeffentlichevergabe.de/ui/de/publicationPolicy';
  original_metadata: {
    title: string | null;
    buyer: string | null;
    description: string | null;
    published_at: string | null;
    deadline: string | null;
    tags: string[];
  };
  mapping_warnings: string[];
}
const text = (v: unknown, size: number): v is string | null =>
  v === null || (typeof v === 'string' && [...v].length <= size);
export function bkmsSourceValid(v: unknown): v is BkmsSource {
  if (!object(v) || !bkmsId(v.external_id) || v.original_url !== bkmsUrl(v.external_id))
    return false;
  try {
    const url = new URL(v.record_url as string);
    const split = v.external_id.lastIndexOf('-');
    if (
      url.origin !== 'https://oeffentlichevergabe.de' ||
      url.username ||
      url.password ||
      url.hash ||
      url.pathname !== `/api/notices/${v.external_id.slice(0, split)}` ||
      [...url.searchParams.keys()].length !== 2 ||
      url.searchParams.get('format') !== 'ocds' ||
      url.searchParams.get('noticeVersion') !== v.external_id.slice(split + 1)
    )
      return false;
  } catch {
    return false;
  }
  const m = v.original_metadata;
  return (
    v.schema_version === 'bkms-opportunity-source-1' &&
    v.source === 'BEKANNTMACHUNGSSERVICE' &&
    text(v.ocid, 150) &&
    typeof v.export_day === 'string' &&
    dateOnly(v.export_day) &&
    timestamp(v.retrieved_at) &&
    digest(v.archive_sha256) &&
    digest(v.record_sha256) &&
    v.license === 'https://opendefinition.org/licenses/cc-zero/' &&
    v.publication_policy === 'https://oeffentlichevergabe.de/ui/de/publicationPolicy' &&
    object(m) &&
    text(m.title, 1000) &&
    text(m.buyer, 500) &&
    text(m.description, 8000) &&
    (m.published_at === null || timestamp(m.published_at)) &&
    (m.deadline === null || timestamp(m.deadline)) &&
    Array.isArray(m.tags) &&
    m.tags.length <= 20 &&
    m.tags.every((t) => typeof t === 'string' && t.length > 0 && t.length <= 50) &&
    Array.isArray(v.mapping_warnings) &&
    v.mapping_warnings.length <= 12 &&
    new Set(v.mapping_warnings).size === v.mapping_warnings.length &&
    v.mapping_warnings.every((w) => bkmsWarnings.includes(w))
  );
}
export function copyBkmsSource(v: BkmsSource): BkmsSource {
  return {
    schema_version: v.schema_version,
    source: v.source,
    external_id: v.external_id,
    original_url: v.original_url,
    record_url: v.record_url,
    ocid: v.ocid,
    export_day: v.export_day,
    retrieved_at: v.retrieved_at,
    archive_sha256: v.archive_sha256,
    record_sha256: v.record_sha256,
    license: v.license,
    publication_policy: v.publication_policy,
    original_metadata: {
      title: v.original_metadata.title,
      buyer: v.original_metadata.buyer,
      description: v.original_metadata.description,
      published_at: v.original_metadata.published_at,
      deadline: v.original_metadata.deadline,
      tags: [...v.original_metadata.tags],
    },
    mapping_warnings: [...v.mapping_warnings],
  };
}
