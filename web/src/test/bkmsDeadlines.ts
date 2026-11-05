import type { DeadlineEvidence, DeadlineSummary } from '../api/bkmsDeadlines';
import { bkmsCatalog } from './bkms';
export function deadlineSummary(loaded = true): DeadlineSummary {
  const c = bkmsCatalog();
  return {
    schema_version: 'bkms-deadline-summary-1',
    catalog_id: c.catalog_id,
    export_day: c.export_day,
    state: loaded ? 'CURRENT' : 'NOT_LOADED',
    enrichment_id: loaded ? '9'.repeat(64) : null,
    bound_catalog_id: loaded ? c.catalog_id : null,
    archive_sha256: loaded ? 'e'.repeat(64) : null,
    retrieved_at: loaded ? '2026-11-05T10:00:00Z' : null,
    extraction_version: 'bkms-eforms-deadlines-1',
    counts: loaded ? { SINGLE: 1 } : {},
  };
}
export function deadlineEvidence(): DeadlineEvidence {
  const c = bkmsCatalog();
  return {
    enrichment_id: '9'.repeat(64),
    catalog_id: c.catalog_id,
    export_day: c.export_day,
    archive_sha256: 'e'.repeat(64),
    retrieved_at: '2026-11-05T10:00:00Z',
    extraction_version: 'bkms-eforms-deadlines-1',
    notice: {
      external_id: '10000001-1',
      record_sha256: 'f'.repeat(64),
      customization_id: 'eforms-de-2.1',
      status: 'SINGLE',
      lots: [
        {
          lot_id: 'LOT-0001',
          status: 'SINGLE',
          periods: [{ dates: ['2026-12-08+01:00'], times: ['10:00:00+01:00'] }],
        },
      ],
      warnings: [],
    },
  };
}
