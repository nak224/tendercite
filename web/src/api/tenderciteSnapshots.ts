import { tenderSnapshotPost } from './tendercite';
import { validSnapshotPeriod, type Period } from '../utils/period';

export const MAX_SNAPSHOT_BYTES = 16 * 1024 * 1024;
export class SnapshotImportError extends Error {}
export interface SnapshotSummary {
  status: 'VALIDATED' | 'INSERTED' | 'ALREADY_PRESENT';
  schema_version: '1.0';
  source_system: 'team-capacity-engine';
  content_hash: string;
  assessment_period: Period;
  member_count: number;
  assignment_count: number;
  catalog_count: number;
  imported_at: string | null;
}

// Check the byte budget before reading. Full JSON/hash/ordering validation stays on the server.
export function readSnapshotFile(file: File): Promise<ArrayBuffer> {
  if (
    !/\.json$/i.test(file.name) ||
    !['', 'application/json', 'text/json', 'application/octet-stream'].includes(file.type) ||
    file.size === 0 ||
    file.size > MAX_SNAPSHOT_BYTES
  )
    return Promise.reject(
      new SnapshotImportError('Select a nonempty .json file no larger than 16 MiB.'),
    );
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    const failed = () =>
      reject(new SnapshotImportError('The selected JSON file could not be read.'));
    reader.onerror = failed;
    reader.onabort = failed;
    reader.onload = () => {
      if (!(reader.result instanceof ArrayBuffer) || reader.result.byteLength !== file.size)
        failed();
      else resolve(reader.result);
    };
    reader.readAsArrayBuffer(file);
  });
}

const safeCodes = new Set([
  'INVALID_CONTENT_LENGTH',
  'INCOMPLETE_BODY',
  'CAPABILITY_SNAPSHOT_API_DISABLED',
  'STORAGE_INTEGRITY',
  'INPUT_TOO_LARGE',
  'UNSUPPORTED_MEDIA_TYPE',
  'UNSUPPORTED_CONTENT_ENCODING',
  'ACKNOWLEDGEMENT_REQUIRED',
  'INVALID_JSON',
  'UNSUPPORTED_SCHEMA',
  'INVALID_CONTRACT',
  'HASH_MISMATCH',
  'DATABASE_FAILED',
  'CAPABILITY_SNAPSHOT_FAILED',
]);
async function responseError(response: Response): Promise<Error> {
  let code = '';
  try {
    const data = await response.json();
    if (typeof data?.detail?.code === 'string' && safeCodes.has(data.detail.code))
      code = `: ${data.detail.code}`;
  } catch {
    // Never render raw workforce records, exception details or proxy response text.
  }
  return new SnapshotImportError(`Snapshot request failed (HTTP ${response.status})${code}.`);
}
function object(value: unknown): value is Record<string, unknown> {
  return !!value && typeof value === 'object' && !Array.isArray(value);
}
function isSummary(value: unknown): value is SnapshotSummary {
  if (!object(value) || !object(value.assessment_period)) return false;
  const period = value.assessment_period;
  return (
    value.schema_version === '1.0' &&
    value.source_system === 'team-capacity-engine' &&
    typeof value.content_hash === 'string' &&
    /^sha256:[a-f0-9]{64}$/.test(value.content_hash) &&
    typeof period.start_date === 'string' &&
    typeof period.end_date === 'string' &&
    validSnapshotPeriod({ start_date: period.start_date, end_date: period.end_date }) &&
    [value.member_count, value.assignment_count, value.catalog_count].every(
      (count) => typeof count === 'number' && Number.isSafeInteger(count) && count >= 0,
    ) &&
    (value.imported_at === null ||
      (typeof value.imported_at === 'string' &&
        /^\d{4}-\d{2}-\d{2}T/.test(value.imported_at) &&
        Number.isFinite(Date.parse(value.imported_at))))
  );
}
export async function sendSnapshot(
  operation: 'validate' | 'import',
  bytes: ArrayBuffer,
  validated?: SnapshotSummary,
  acknowledged = false,
): Promise<SnapshotSummary> {
  if (!bytes.byteLength || bytes.byteLength > MAX_SNAPSHOT_BYTES)
    throw new SnapshotImportError('Select a nonempty .json file no larger than 16 MiB.');
  if (operation === 'import' && (!acknowledged || validated?.status !== 'VALIDATED'))
    throw new SnapshotImportError(
      'Validate the selected file and acknowledge it before importing.',
    );
  let result: unknown;
  try {
    result = await tenderSnapshotPost(operation, bytes, responseError);
  } catch (error) {
    if (error instanceof SnapshotImportError) throw error;
    throw new SnapshotImportError('TenderCite snapshot response unavailable or invalid.');
  }
  if (
    !isSummary(result) ||
    (operation === 'validate'
      ? result.status !== 'VALIDATED' || result.imported_at !== null
      : (result.status !== 'INSERTED' && result.status !== 'ALREADY_PRESENT') ||
        result.imported_at === null) ||
    (validated &&
      (result.content_hash !== validated.content_hash ||
        result.schema_version !== validated.schema_version ||
        result.source_system !== validated.source_system ||
        result.assessment_period.start_date !== validated.assessment_period.start_date ||
        result.assessment_period.end_date !== validated.assessment_period.end_date ||
        result.member_count !== validated.member_count ||
        result.assignment_count !== validated.assignment_count ||
        result.catalog_count !== validated.catalog_count))
  )
    throw new SnapshotImportError('TenderCite returned an invalid or mismatched snapshot summary.');
  // Project only safe metadata; do not retain additional response fields/workforce records.
  return {
    status: result.status,
    schema_version: result.schema_version,
    source_system: result.source_system,
    content_hash: result.content_hash,
    assessment_period: {
      start_date: result.assessment_period.start_date,
      end_date: result.assessment_period.end_date,
    },
    member_count: result.member_count,
    assignment_count: result.assignment_count,
    catalog_count: result.catalog_count,
    imported_at: result.imported_at,
  };
}
