// Self-authored TED-shaped fixtures. IDs refer to fictional future publications, never live data.
import { emptyOpportunity, type OpportunityRecord } from '../api/opportunities';
import type { TedItem, TedSearchResult } from '../api/discovery';
export const tedId = '99999991-2099';
export const importedId = '50000000-0000-0000-0000-000000000001';
export function tedItem(): TedItem {
  return {
    publication_id: tedId,
    source: 'TED',
    title: 'Synthetic software services',
    organization: 'Fictional public buyer',
    summary: 'Original synthetic procurement summary.',
    publication_date: '2099-02-01',
    submission_deadline: '2099-03-01',
    source_url: `https://ted.europa.eu/de/notice/-/detail/${tedId}`,
    retrieved_at: '2026-10-30T10:00:00Z',
    importable: true,
    warnings: [],
  };
}
export function tedResults(): TedSearchResult {
  return {
    schema_version: 'ted-search-result-1',
    source: 'TED',
    scope: 'ACTIVE_COMPETITION',
    keyword: 'Software',
    page: 1,
    limit: 10,
    total_notice_count: 1,
    items: [tedItem()],
    next_page: null,
    pagination_capped: false,
  };
}
export function tedRecord(): OpportunityRecord {
  const item = tedItem();
  return {
    schema_version: 'native-opportunity-record-1',
    opportunity_id: importedId,
    revision: 1,
    created_at: '2026-10-30T10:00:00Z',
    updated_at: '2026-10-30T10:00:00Z',
    opportunity: {
      ...emptyOpportunity(),
      title: item.title!,
      opportunity_type: 'PUBLIC_TENDER',
      organization: item.organization!,
      description: item.summary!,
      deadline: item.submission_deadline,
      source_platform: 'TED',
      source_url: item.source_url,
      source_origin: 'ted_import',
      external_source: {
        schema_version: 'ted-opportunity-source-1',
        source: 'TED',
        external_id: tedId,
        original_url: item.source_url,
        publication_date: item.publication_date,
        retrieved_at: item.retrieved_at,
        mapping_warnings: [],
      },
    },
  };
}
