// Self-authored synthetic notice metadata. No live procurement text or personnel.
import { emptyOpportunity, type OpportunityRecord } from '../api/opportunities';
import type { BkmsSource } from '../api/bkmsReferences';
import type { BkmsCatalog, BkmsResults } from '../api/bkms';
import type { BkmsCollection, MultiBkmsResults } from '../api/bkmsCatalogs';
export const bkmsNotice = '10000001-1';
export const bkmsOpportunity = '60000000-0000-0000-0000-000000000001';
export function bkmsCatalog(): BkmsCatalog {
  return {
    schema_version: 'bkms-catalog-summary-1',
    catalog_id: 'a'.repeat(64),
    export_day: '2026-10-30',
    retrieved_at: '2026-10-31T10:00:00Z',
    archive_sha256: 'b'.repeat(64),
    record_count: 1,
    importable_count: 1,
    warning_count: 0,
  };
}
export function bkmsSource(): BkmsSource {
  return {
    schema_version: 'bkms-opportunity-source-1',
    source: 'BEKANNTMACHUNGSSERVICE',
    external_id: bkmsNotice,
    original_url: 'https://oeffentlichevergabe.de/ui/notices/10000001?noticeVersion=1',
    record_url: 'https://oeffentlichevergabe.de/api/notices/10000001?format=ocds&noticeVersion=1',
    ocid: 'ocds-mnwr74-synthetic-10000001',
    export_day: '2026-10-30',
    retrieved_at: '2026-10-31T10:00:00Z',
    archive_sha256: 'b'.repeat(64),
    record_sha256: 'c'.repeat(64),
    license: 'https://opendefinition.org/licenses/cc-zero/',
    publication_policy: 'https://oeffentlichevergabe.de/ui/de/publicationPolicy',
    original_metadata: {
      title: 'Synthetic software procurement',
      buyer: 'Fictional public buyer',
      description: 'Original synthetic Python procurement description.',
      published_at: '2026-10-30T08:00:00Z',
      deadline: '2026-12-04T12:00:00+01:00',
      tags: ['tender'],
    },
    mapping_warnings: [],
  };
}
export function bkmsResults(): BkmsResults {
  return {
    schema_version: 'bkms-search-result-1',
    catalog: bkmsCatalog(),
    keyword: 'Software',
    page: 1,
    limit: 10,
    total_count: 1,
    items: [bkmsSource()],
    next_page: null,
  };
}
export function bkmsRecord(): OpportunityRecord {
  const s = bkmsSource();
  return {
    schema_version: 'native-opportunity-record-1',
    opportunity_id: bkmsOpportunity,
    revision: 1,
    created_at: s.retrieved_at,
    updated_at: s.retrieved_at,
    opportunity: {
      ...emptyOpportunity(),
      title: s.original_metadata.title!,
      opportunity_type: 'PUBLIC_TENDER',
      organization: s.original_metadata.buyer!,
      description: s.original_metadata.description!,
      deadline: '2026-12-04',
      source_platform: 'Bekanntmachungsservice',
      source_url: s.original_url,
      source_origin: 'bkms_import',
      external_source: s,
    },
  };
}

export function bkmsCollection(empty = false): BkmsCollection {
  return {
    schema_version: 'bkms-catalog-collection-1',
    collection_id: (empty ? 'e' : 'd').repeat(64),
    days: empty ? [] : [{ catalog: bkmsCatalog(), warning_counts: {} }],
    stored_bytes: empty ? 0 : 2000,
    max_days: 7,
    max_storage_bytes: 100663296,
  };
}
export function bkmsMultiResults(): MultiBkmsResults {
  return {
    schema_version: 'bkms-search-result-2',
    coverage: bkmsCollection(),
    keyword: 'Software',
    page: 1,
    limit: 10,
    notice_category: 'ALL',
    include_earlier_versions: false,
    total_count: 1,
    next_page: null,
    items: [
      {
        source: bkmsSource(),
        notice_category: 'TENDER',
        version_count: 1,
        is_latest_stored: true,
        occurrences: [
          { export_day: '2026-10-30', catalog_id: 'a'.repeat(64), record_sha256: 'c'.repeat(64) },
        ],
        source_record_hashes_differ: false,
      },
    ],
  };
}
