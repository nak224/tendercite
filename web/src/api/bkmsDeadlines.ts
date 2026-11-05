import { object, count, timestamp } from './capabilityMappingValidation';
import { BkmsError, bkmsRequest, bkmsResponseError, type BkmsCatalog } from './bkms';
import { digest, type BkmsSource } from './bkmsReferences';
import { dateOnly } from './tedReferences';
import { tenderGet } from './tendercite';

const statuses = ['SINGLE', 'MULTIPLE_LOTS', 'MISSING', 'CONFLICT', 'UNRECOGNIZED'] as const;
export type DeadlineStatus = (typeof statuses)[number];
const warnings = [
  'UNSUPPORTED_CUSTOMIZATION',
  'UNRECOGNIZED_STRUCTURE',
  'MISSING_SUBMISSION_DEADLINE',
  'MISSING_TIME',
  'MISSING_TIMEZONE',
  'OFFSET_CONFLICT',
  'CONFLICTING_VALUES',
  'DUPLICATE_LOT',
];
export interface SourcePeriod {
  dates: string[];
  times: string[];
}
export interface LotDeadline {
  lot_id: string;
  status: DeadlineStatus;
  periods: SourcePeriod[];
}
export interface DeadlineEvidence {
  enrichment_id: string;
  catalog_id: string;
  export_day: string;
  archive_sha256: string;
  retrieved_at: string;
  extraction_version: 'bkms-eforms-deadlines-1';
  notice: {
    external_id: string;
    record_sha256: string;
    customization_id: string;
    status: DeadlineStatus;
    lots: LotDeadline[];
    warnings: string[];
  };
}
export interface DeadlineSummary {
  schema_version: 'bkms-deadline-summary-1';
  state: 'CURRENT' | 'STALE' | 'NOT_LOADED';
  catalog_id: string;
  export_day: string;
  enrichment_id: string | null;
  bound_catalog_id: string | null;
  archive_sha256: string | null;
  retrieved_at: string | null;
  extraction_version: 'bkms-eforms-deadlines-1';
  counts: Partial<Record<DeadlineStatus, number>>;
}
const status = (v: unknown): v is DeadlineStatus =>
  typeof v === 'string' && statuses.includes(v as DeadlineStatus);
const rawValues = (v: unknown): v is string[] =>
  Array.isArray(v) && v.length <= 4 && v.every((x) => typeof x === 'string' && x.length <= 40);
function invalid(): never {
  throw new BkmsError('Invalid or mismatched official deadline metadata.');
}
export function parseDeadlineEvidence(
  v: unknown,
  source: BkmsSource,
  catalogId: string,
): DeadlineEvidence {
  if (
    !object(v) ||
    !digest(v.enrichment_id) ||
    v.catalog_id !== catalogId ||
    v.export_day !== source.export_day ||
    !digest(v.archive_sha256) ||
    !timestamp(v.retrieved_at) ||
    v.extraction_version !== 'bkms-eforms-deadlines-1' ||
    !object(v.notice)
  )
    invalid();
  const n = v.notice;
  if (
    n.external_id !== source.external_id ||
    !digest(n.record_sha256) ||
    typeof n.customization_id !== 'string' ||
    n.customization_id.length > 80 ||
    !status(n.status) ||
    !Array.isArray(n.lots) ||
    n.lots.length > 128 ||
    !Array.isArray(n.warnings) ||
    n.warnings.length > 8 ||
    n.warnings.some((x) => typeof x !== 'string' || !warnings.includes(x)) ||
    new Set(n.warnings).size !== n.warnings.length ||
    (n.customization_id !== 'eforms-de-2.1' && n.status !== 'UNRECOGNIZED')
  )
    invalid();
  const lots: LotDeadline[] = n.lots.map((lot: unknown) => {
    if (
      !object(lot) ||
      typeof lot.lot_id !== 'string' ||
      !lot.lot_id ||
      lot.lot_id.length > 50 ||
      !status(lot.status) ||
      !Array.isArray(lot.periods) ||
      lot.periods.length > 4
    )
      invalid();
    const periods = lot.periods.map((p: unknown) => {
      if (!object(p) || !rawValues(p.dates) || !rawValues(p.times)) invalid();
      return { dates: [...p.dates], times: [...p.times] };
    });
    return { lot_id: lot.lot_id, status: lot.status, periods };
  });
  return {
    enrichment_id: v.enrichment_id,
    catalog_id: v.catalog_id as string,
    export_day: source.export_day,
    archive_sha256: v.archive_sha256,
    retrieved_at: v.retrieved_at,
    extraction_version: 'bkms-eforms-deadlines-1',
    notice: {
      external_id: source.external_id,
      record_sha256: n.record_sha256,
      customization_id: n.customization_id,
      status: n.status,
      lots,
      warnings: [...n.warnings] as string[],
    },
  };
}
function summary(v: unknown, catalog: BkmsCatalog): DeadlineSummary {
  if (
    !object(v) ||
    v.schema_version !== 'bkms-deadline-summary-1' ||
    v.catalog_id !== catalog.catalog_id ||
    v.export_day !== catalog.export_day ||
    v.extraction_version !== 'bkms-eforms-deadlines-1' ||
    !['CURRENT', 'STALE', 'NOT_LOADED'].includes(v.state as string) ||
    !object(v.counts)
  )
    invalid();
  const counts = Object.entries(v.counts);
  if (
    counts.length > 5 ||
    counts.some(([key, value]) => !status(key) || !count(value) || value < 1 || value > 4000)
  )
    invalid();
  if (v.state === 'NOT_LOADED') {
    if (
      [v.enrichment_id, v.bound_catalog_id, v.archive_sha256, v.retrieved_at].some(
        (x) => x !== null,
      ) ||
      counts.length
    )
      invalid();
  } else if (
    !digest(v.enrichment_id) ||
    !digest(v.bound_catalog_id) ||
    !digest(v.archive_sha256) ||
    !timestamp(v.retrieved_at) ||
    (v.state === 'CURRENT') !== (v.bound_catalog_id === catalog.catalog_id) ||
    (v.state === 'CURRENT' &&
      counts.reduce((n, [, c]) => n + (c as number), 0) !== catalog.record_count)
  )
    invalid();
  return {
    schema_version: 'bkms-deadline-summary-1',
    state: v.state as DeadlineSummary['state'],
    catalog_id: catalog.catalog_id,
    export_day: catalog.export_day,
    enrichment_id: v.enrichment_id as string | null,
    bound_catalog_id: v.bound_catalog_id as string | null,
    archive_sha256: v.archive_sha256 as string | null,
    retrieved_at: v.retrieved_at as string | null,
    extraction_version: 'bkms-eforms-deadlines-1',
    counts: Object.fromEntries(counts) as DeadlineSummary['counts'],
  };
}
export async function readDeadlineSummary(catalog: BkmsCatalog): Promise<DeadlineSummary> {
  if (!digest(catalog.catalog_id) || !dateOnly(catalog.export_day)) invalid();
  try {
    return summary(
      await tenderGet<unknown>(
        `discovery/bkms/deadlines/${catalog.catalog_id}`,
        new AbortController().signal,
        bkmsResponseError,
      ),
      catalog,
    );
  } catch (error) {
    if (error instanceof BkmsError) throw error;
    throw new BkmsError('Stored deadline metadata unavailable. No automatic retry was made.');
  }
}
export async function loadDeadlines(
  catalog: BkmsCatalog,
  expected: string | null,
): Promise<DeadlineSummary> {
  if (!digest(catalog.catalog_id) || (expected !== null && !digest(expected))) invalid();
  const result = summary(
    await bkmsRequest('deadlines/load', {
      schema_version: 'bkms-deadline-load-1',
      catalog_id: catalog.catalog_id,
      ...(expected ? { expected_enrichment_id: expected } : {}),
    }),
    catalog,
  );
  if (result.state !== 'CURRENT') invalid();
  return result;
}
