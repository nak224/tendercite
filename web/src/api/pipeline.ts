import { tenderGet, tenderPipelineWrite } from './tendercite';
import { object, uuid, count, timestamp } from './capabilityMappingValidation';
import { opportunityMetadataValid, type OpportunityMetadata } from './opportunities';
import { validDate } from '../utils/period';

export const pipelineStages = [
  'SAVED',
  'REVIEWING',
  'PREPARING',
  'SUBMITTED',
  'WON',
  'LOST',
  'ARCHIVED',
] as const;
export type PipelineStage = (typeof pipelineStages)[number];
export const pipelinePriorities = ['LOW', 'MEDIUM', 'HIGH'] as const;
export type PipelinePriority = (typeof pipelinePriorities)[number];
export interface PipelineValues {
  stage: PipelineStage;
  priority: PipelinePriority;
  notes: string;
  next_action: string;
  next_action_due_date: string | null;
}
export interface PipelineRecord {
  schema_version: 'native-pipeline-record-1';
  pipeline_id: string;
  opportunity_id: string;
  revision: number;
  created_at: string;
  updated_at: string;
  values: PipelineValues;
}
export interface PipelineEntry {
  schema_version: 'native-pipeline-entry-1';
  record: PipelineRecord;
  opportunity: OpportunityMetadata;
}
export interface PipelineListing {
  schema_version: 'native-pipeline-listing-1';
  entries: PipelineEntry[];
  next_offset: number | null;
}
export class PipelineInputError extends Error {}
export class PipelineRequestError extends Error {}
const codes = new Set([
  'PIPELINE_API_DISABLED',
  'INVALID_PIPELINE_INPUT',
  'PIPELINE_NOT_FOUND',
  'OPPORTUNITY_NOT_FOUND',
  'PIPELINE_ID_CONFLICT',
  'PIPELINE_REVISION_CONFLICT',
  'PIPELINE_STORAGE_INTEGRITY',
  'OPPORTUNITY_STORAGE_INTEGRITY',
  'DATABASE_FAILED',
  'PIPELINE_REQUEST_TOO_LARGE',
  'PIPELINE_OPERATION_FAILED',
  'INVALID_CONTENT_LENGTH',
  'INCOMPLETE_BODY',
  'UNSUPPORTED_MEDIA_TYPE',
  'UNSUPPORTED_CONTENT_ENCODING',
]);
async function responseError(response: Response): Promise<Error> {
  let code = '';
  try {
    const body = await response.json();
    if (typeof body?.detail?.code === 'string' && codes.has(body.detail.code))
      code = body.detail.code;
  } catch {
    /* No raw response content or untrusted field names. */
  }
  return new PipelineRequestError(
    `Pipeline request failed (HTTP ${response.status})${code ? `: ${code}` : ''}.`,
  );
}
const text = (v: unknown, max: number): v is string =>
  typeof v === 'string' && [...v].length <= max;
const revision = (v: unknown): v is number => count(v) && v >= 1 && v <= 2147483647;
function valuesValid(v: unknown): v is PipelineValues {
  return (
    object(v) &&
    pipelineStages.includes(v.stage as PipelineStage) &&
    pipelinePriorities.includes(v.priority as PipelinePriority) &&
    text(v.notes, 4000) &&
    text(v.next_action, 1000) &&
    (v.next_action_due_date === null ||
      (typeof v.next_action_due_date === 'string' &&
        !v.next_action_due_date.startsWith('0000-') &&
        validDate(v.next_action_due_date)))
  );
}
export function pipelineInput(v: PipelineValues): PipelineValues {
  const input = { ...v, notes: v.notes.trim(), next_action: v.next_action.trim() };
  if (!valuesValid(input))
    throw new PipelineInputError(
      'Select a supported stage/priority, at most 4,000 characters of notes, 1,000 characters for the next action and a valid date-only due date.',
    );
  return {
    stage: input.stage,
    priority: input.priority,
    notes: input.notes,
    next_action: input.next_action,
    next_action_due_date: input.next_action_due_date,
  };
}
export function defaultPipelineValues(): PipelineValues {
  return {
    stage: 'SAVED',
    priority: 'MEDIUM',
    notes: '',
    next_action: '',
    next_action_due_date: null,
  };
}
function sameValues(a: PipelineValues, b: PipelineValues): boolean {
  return (
    a.stage === b.stage &&
    a.priority === b.priority &&
    a.notes === b.notes &&
    a.next_action === b.next_action &&
    a.next_action_due_date === b.next_action_due_date
  );
}
function entryValid(v: unknown): v is PipelineEntry {
  if (
    !object(v) ||
    v.schema_version !== 'native-pipeline-entry-1' ||
    !object(v.record) ||
    !opportunityMetadataValid(v.opportunity)
  )
    return false;
  const r = v.record;
  return (
    r.schema_version === 'native-pipeline-record-1' &&
    uuid(r.pipeline_id) &&
    uuid(r.opportunity_id) &&
    r.opportunity_id === v.opportunity.opportunity_id &&
    revision(r.revision) &&
    timestamp(r.created_at) &&
    timestamp(r.updated_at) &&
    Date.parse(r.created_at) <= Date.parse(r.updated_at) &&
    valuesValid(r.values)
  );
}
function copyEntry(v: PipelineEntry): PipelineEntry {
  const { record: r, opportunity: o } = v;
  return {
    schema_version: v.schema_version,
    record: {
      schema_version: r.schema_version,
      pipeline_id: r.pipeline_id,
      opportunity_id: r.opportunity_id,
      revision: r.revision,
      created_at: r.created_at,
      updated_at: r.updated_at,
      values: {
        stage: r.values.stage,
        priority: r.values.priority,
        notes: r.values.notes,
        next_action: r.values.next_action,
        next_action_due_date: r.values.next_action_due_date,
      },
    },
    opportunity: {
      opportunity_id: o.opportunity_id,
      revision: o.revision,
      title: o.title,
      opportunity_type: o.opportunity_type,
      organization: o.organization,
      deadline: o.deadline,
      updated_at: o.updated_at,
    },
  };
}
async function get(path: string, signal: AbortSignal): Promise<unknown> {
  try {
    return await tenderGet(path, signal, responseError);
  } catch (error) {
    if (signal.aborted || error instanceof PipelineRequestError) throw error;
    throw new PipelineRequestError('Pipeline data unavailable or invalid. Reload explicitly.');
  }
}
export async function listPipeline(
  offset: number,
  stage: PipelineStage | '',
  signal: AbortSignal,
  opportunityId?: string,
): Promise<PipelineListing> {
  if (
    !count(offset) ||
    offset > 1000000 ||
    (stage && !pipelineStages.includes(stage)) ||
    (opportunityId !== undefined && !uuid(opportunityId))
  )
    throw new PipelineInputError('Invalid pipeline selection.');
  const query = new URLSearchParams({ limit: '50', offset: String(offset) });
  if (stage) query.set('stage', stage);
  if (opportunityId) query.set('opportunity_id', opportunityId);
  const v = await get(`pipeline?${query}`, signal);
  if (
    !object(v) ||
    v.schema_version !== 'native-pipeline-listing-1' ||
    !Array.isArray(v.entries) ||
    v.entries.length > 50 ||
    !(v.next_offset === null || v.next_offset === offset + 50) ||
    !v.entries.every(entryValid) ||
    !v.entries.every(
      (e) =>
        (!stage || e.record.values.stage === stage) &&
        (!opportunityId || e.record.opportunity_id === opportunityId),
    ) ||
    new Set(v.entries.map((e) => e.record.pipeline_id)).size !== v.entries.length ||
    new Set(v.entries.map((e) => e.record.opportunity_id)).size !== v.entries.length
  )
    throw new PipelineRequestError('Invalid or mismatched pipeline listing.');
  return {
    schema_version: v.schema_version,
    entries: v.entries.map(copyEntry),
    next_offset: v.next_offset as number | null,
  };
}
export async function getPipelineEntry(id: string, signal: AbortSignal): Promise<PipelineEntry> {
  if (!uuid(id)) throw new PipelineInputError('Select an existing pipeline entry.');
  const result = await get(`pipeline/${encodeURIComponent(id)}`, signal);
  if (!entryValid(result) || result.record.pipeline_id !== id)
    throw new PipelineRequestError('Invalid or mismatched pipeline response.');
  return copyEntry(result);
}
async function write(operation: 'create' | 'update', id: string, input: object): Promise<unknown> {
  const body = JSON.stringify(input);
  if (new TextEncoder().encode(body).byteLength > 65536)
    throw new PipelineInputError('Pipeline request exceeds the 65,536-byte limit.');
  try {
    return await tenderPipelineWrite(operation, id, body, responseError);
  } catch (error) {
    if (error instanceof PipelineRequestError) throw error;
    throw new PipelineRequestError(
      'Pipeline save response unavailable or invalid. No automatic retry was made.',
    );
  }
}
export async function addPipelineEntry(
  id: string,
  opportunityId: string,
): Promise<{ status: 'CREATED' | 'ALREADY_PRESENT'; entry: PipelineEntry }> {
  if (!uuid(id) || !uuid(opportunityId))
    throw new PipelineInputError('Select an existing opportunity.');
  const result = await write('create', id, {
    schema_version: 'native-pipeline-create-1',
    pipeline_id: id,
    opportunity_id: opportunityId,
  });
  if (
    !object(result) ||
    result.schema_version !== 'native-pipeline-save-result-1' ||
    !['CREATED', 'ALREADY_PRESENT'].includes(result.status as string) ||
    !entryValid(result.entry) ||
    result.entry.record.opportunity_id !== opportunityId ||
    (result.status === 'CREATED' &&
      (result.entry.record.pipeline_id !== id ||
        result.entry.record.revision !== 1 ||
        !sameValues(result.entry.record.values, defaultPipelineValues())))
  )
    throw new PipelineRequestError('Invalid or mismatched pipeline creation response.');
  return { status: result.status as 'CREATED' | 'ALREADY_PRESENT', entry: copyEntry(result.entry) };
}
export async function updatePipelineEntry(
  current: PipelineEntry,
  input: PipelineValues,
): Promise<PipelineEntry> {
  if (!entryValid(current)) throw new PipelineInputError('Load a saved pipeline entry.');
  const values = pipelineInput(input);
  const result = await write('update', current.record.pipeline_id, {
    schema_version: 'native-pipeline-update-1',
    expected_revision: current.record.revision,
    values,
  });
  if (
    !object(result) ||
    result.schema_version !== 'native-pipeline-save-result-1' ||
    result.status !== 'UPDATED' ||
    !entryValid(result.entry) ||
    result.entry.record.pipeline_id !== current.record.pipeline_id ||
    result.entry.record.opportunity_id !== current.record.opportunity_id ||
    result.entry.record.revision !== current.record.revision + 1 ||
    result.entry.record.created_at !== current.record.created_at ||
    !sameValues(result.entry.record.values, values)
  )
    throw new PipelineRequestError('Invalid or mismatched pipeline save response.');
  return copyEntry(result.entry);
}
