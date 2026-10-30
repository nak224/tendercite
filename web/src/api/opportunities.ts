import { tenderGet, tenderOpportunityWrite } from './tendercite';
import { object, uuid, count, timestamp, nonblank } from './capabilityMappingValidation';
import { validDate } from '../utils/period';
import { copyTedSource, tedSourceValid, type TedSource } from './tedReferences';

export type OpportunityType = 'PUBLIC_TENDER' | 'FREELANCE_PROJECT' | 'OTHER';
export type WorkArrangement = 'REMOTE' | 'HYBRID' | 'ON_SITE' | null;
export interface NativeOpportunity {
  schema_version: 'native-opportunity-1';
  title: string;
  opportunity_type: OpportunityType;
  organization: string;
  description: string;
  location: string;
  work_arrangement: WorkArrangement;
  deadline: string | null;
  source_platform: string;
  source_url: string;
  required_skills: string[];
  requirements_description: string;
  source_origin: 'manual_operator_entry' | 'ted_import';
  external_source?: TedSource | null;
  verification_status: 'NOT_VERIFIED';
}
export interface OpportunityRecord {
  schema_version: 'native-opportunity-record-1';
  opportunity_id: string;
  revision: number;
  created_at: string;
  updated_at: string;
  opportunity: NativeOpportunity;
}
export type OpportunityMetadata = Pick<
  OpportunityRecord,
  'opportunity_id' | 'revision' | 'updated_at'
> &
  Pick<NativeOpportunity, 'title' | 'opportunity_type' | 'organization' | 'deadline'>;
export interface OpportunityListing {
  schema_version: 'native-opportunity-listing-1';
  opportunities: OpportunityMetadata[];
  next_offset: number | null;
}
export class OpportunityInputError extends Error {}
export class OpportunityRequestError extends Error {
  readonly code: string;
  constructor(message: string, code = '') {
    super(message);
    this.code = code;
  }
}
const safeCodes = new Set([
  'OPPORTUNITIES_API_DISABLED',
  'INVALID_OPPORTUNITY_INPUT',
  'OPPORTUNITY_NOT_FOUND',
  'OPPORTUNITY_ID_CONFLICT',
  'OPPORTUNITY_REVISION_CONFLICT',
  'OPPORTUNITY_STORAGE_INTEGRITY',
  'OPPORTUNITY_REQUEST_TOO_LARGE',
  'OPPORTUNITY_OPERATION_FAILED',
  'OPPORTUNITY_PROVENANCE_CONFLICT',
  'DATABASE_FAILED',
  'INVALID_CONTENT_LENGTH',
  'INCOMPLETE_BODY',
  'UNSUPPORTED_MEDIA_TYPE',
  'UNSUPPORTED_CONTENT_ENCODING',
]);
async function responseError(response: Response): Promise<Error> {
  let code = '';
  try {
    const body = await response.json();
    if (typeof body?.detail?.code === 'string' && safeCodes.has(body.detail.code))
      code = body.detail.code;
  } catch {
    /* Never expose raw backend/proxy errors or untrusted fields. */
  }
  return new OpportunityRequestError(
    `Opportunity request failed (HTTP ${response.status})${code ? ': ' + code : ''}.`,
    code,
  );
}
export function sourceUrlValid(value: string): boolean {
  if (value === '') return true;
  if (
    !/^https?:\/\//i.test(value) ||
    /[\s\\]/u.test(value) ||
    [...value].some((c) => c.codePointAt(0)! < 32 || c.codePointAt(0) === 127) ||
    /^https?:\/\/[^/?#]*@/i.test(value)
  )
    return false;
  try {
    const url = new URL(value);
    return (
      ['http:', 'https:'].includes(url.protocol) &&
      !!url.hostname &&
      !url.username &&
      !url.password &&
      url.port !== '0'
    );
  } catch {
    return false;
  }
}
const text = (v: unknown, max: number): v is string =>
  typeof v === 'string' && [...v].length <= max;
const kind = (v: unknown): v is OpportunityType =>
  v === 'PUBLIC_TENDER' || v === 'FREELANCE_PROJECT' || v === 'OTHER';
const date = (v: unknown): v is string | null =>
  v === null || (typeof v === 'string' && !v.startsWith('0000-') && validDate(v));
const key = (s: string) => s.normalize('NFKC').toLowerCase().replace(/\s+/gu, ' ').trim();
export function opportunityMetadataValid(v: unknown): v is OpportunityMetadata {
  return (
    object(v) &&
    uuid(v.opportunity_id) &&
    count(v.revision) &&
    v.revision >= 1 &&
    v.revision <= 2147483647 &&
    nonblank(v.title, 200) &&
    kind(v.opportunity_type) &&
    text(v.organization, 200) &&
    date(v.deadline) &&
    timestamp(v.updated_at)
  );
}
function contentValid(v: unknown): v is NativeOpportunity {
  return (
    object(v) &&
    v.schema_version === 'native-opportunity-1' &&
    nonblank(v.title, 200) &&
    kind(v.opportunity_type) &&
    text(v.organization, 200) &&
    text(v.description, 2000) &&
    text(v.location, 120) &&
    [null, 'REMOTE', 'HYBRID', 'ON_SITE'].includes(v.work_arrangement as string | null) &&
    date(v.deadline) &&
    text(v.source_platform, 200) &&
    text(v.source_url, 2048) &&
    sourceUrlValid(v.source_url) &&
    text(v.requirements_description, 2000) &&
    ((v.source_origin === 'manual_operator_entry' &&
      (v.external_source === undefined || v.external_source === null)) ||
      (v.source_origin === 'ted_import' && tedSourceValid(v.external_source))) &&
    v.verification_status === 'NOT_VERIFIED' &&
    Array.isArray(v.required_skills) &&
    v.required_skills.length <= 100 &&
    v.required_skills.every((s) => nonblank(s, 100)) &&
    new Set(v.required_skills.map(key)).size === v.required_skills.length
  );
}
function copyOpportunity(v: NativeOpportunity): NativeOpportunity {
  return {
    schema_version: v.schema_version,
    title: v.title,
    opportunity_type: v.opportunity_type,
    organization: v.organization,
    description: v.description,
    location: v.location,
    work_arrangement: v.work_arrangement,
    deadline: v.deadline,
    source_platform: v.source_platform,
    source_url: v.source_url,
    required_skills: [...v.required_skills],
    requirements_description: v.requirements_description,
    source_origin: v.source_origin,
    ...(v.source_origin === 'ted_import' && v.external_source
      ? { external_source: copyTedSource(v.external_source) }
      : {}),
    verification_status: v.verification_status,
  };
}
export function opportunityInput(v: NativeOpportunity): NativeOpportunity {
  const normalized = {
    ...v,
    title: v.title.trim(),
    organization: v.organization.trim(),
    description: v.description.trim(),
    location: v.location.trim(),
    source_platform: v.source_platform.trim(),
    source_url: v.source_url.trim(),
    required_skills: v.required_skills.map((s) => s.trim()),
    requirements_description: v.requirements_description.trim(),
  };
  if (!contentValid(normalized))
    throw new OpportunityInputError(
      'Enter a title (1–200 characters), valid type, optional HTTP/HTTPS source URL without credentials, valid date-only deadline, and at most 100 unique skill labels (1–100 characters each). Organization/source: maximum 200; location: 120; descriptions: 2000.',
    );
  return copyOpportunity(normalized);
}
function recordValid(v: unknown, id: string): v is OpportunityRecord {
  return (
    object(v) &&
    v.schema_version === 'native-opportunity-record-1' &&
    v.opportunity_id === id &&
    uuid(v.opportunity_id) &&
    count(v.revision) &&
    v.revision >= 1 &&
    v.revision <= 2147483647 &&
    timestamp(v.created_at) &&
    timestamp(v.updated_at) &&
    Date.parse(v.created_at) <= Date.parse(v.updated_at) &&
    contentValid(v.opportunity)
  );
}
function copyRecord(v: OpportunityRecord): OpportunityRecord {
  return {
    schema_version: v.schema_version,
    opportunity_id: v.opportunity_id,
    revision: v.revision,
    created_at: v.created_at,
    updated_at: v.updated_at,
    opportunity: copyOpportunity(v.opportunity),
  };
}
export function parseOpportunityRecord(value: unknown, id: string): OpportunityRecord {
  if (!recordValid(value, id))
    throw new OpportunityRequestError('Invalid or mismatched opportunity response.');
  return copyRecord(value);
}
async function get(path: string, signal: AbortSignal): Promise<unknown> {
  try {
    return await tenderGet(path, signal, responseError);
  } catch (error) {
    if (signal.aborted || error instanceof OpportunityRequestError) throw error;
    throw new OpportunityRequestError(
      'Opportunity data unavailable or invalid. Reload explicitly.',
    );
  }
}
export async function listOpportunities(
  offset: number,
  signal: AbortSignal,
): Promise<OpportunityListing> {
  if (!count(offset) || offset > 1000000)
    throw new OpportunityInputError('Invalid opportunity page.');
  const value = await get(`opportunities?limit=50&offset=${offset}`, signal);
  if (
    !object(value) ||
    value.schema_version !== 'native-opportunity-listing-1' ||
    !Array.isArray(value.opportunities) ||
    value.opportunities.length > 50 ||
    !(value.next_offset === null || value.next_offset === offset + 50) ||
    !value.opportunities.every(opportunityMetadataValid) ||
    new Set(value.opportunities.map((v) => v.opportunity_id)).size !== value.opportunities.length
  )
    throw new OpportunityRequestError('Invalid opportunity listing.');
  return {
    schema_version: value.schema_version,
    next_offset: value.next_offset as number | null,
    opportunities: value.opportunities.map((v) => ({
      opportunity_id: v.opportunity_id,
      revision: v.revision,
      title: v.title,
      opportunity_type: v.opportunity_type,
      organization: v.organization,
      deadline: v.deadline,
      updated_at: v.updated_at,
    })),
  };
}
export async function getOpportunity(id: string, signal: AbortSignal): Promise<OpportunityRecord> {
  if (!uuid(id)) throw new OpportunityInputError('Select an existing opportunity.');
  const value = await get(`opportunities/${encodeURIComponent(id)}`, signal);
  if (!recordValid(value, id))
    throw new OpportunityRequestError('Invalid or mismatched opportunity response.');
  return copyRecord(value);
}
export async function saveOpportunity(
  id: string,
  input: NativeOpportunity,
  expectedRevision?: number,
): Promise<{ status: 'CREATED' | 'ALREADY_PRESENT' | 'UPDATED'; record: OpportunityRecord }> {
  const opportunity = opportunityInput(input);
  if (
    !uuid(id) ||
    (expectedRevision !== undefined &&
      (!count(expectedRevision) || expectedRevision < 1 || expectedRevision > 2147483647))
  )
    throw new OpportunityInputError('Invalid opportunity identity or revision.');
  const create = expectedRevision === undefined;
  const body = JSON.stringify(
    create
      ? { schema_version: 'native-opportunity-create-1', opportunity_id: id, opportunity }
      : {
          schema_version: 'native-opportunity-update-1',
          expected_revision: expectedRevision,
          opportunity,
        },
  );
  if (new TextEncoder().encode(body).byteLength > 65536)
    throw new OpportunityInputError('Opportunity request exceeds the 65,536-byte limit.');
  let value: unknown;
  try {
    value = await tenderOpportunityWrite(create ? 'create' : 'update', id, body, responseError);
  } catch (error) {
    if (error instanceof OpportunityRequestError) throw error;
    throw new OpportunityRequestError(
      'Opportunity save response unavailable or invalid. No automatic retry was made.',
    );
  }
  if (
    !object(value) ||
    value.schema_version !== 'native-opportunity-save-result-1' ||
    !(create
      ? value.status === 'CREATED' || value.status === 'ALREADY_PRESENT'
      : value.status === 'UPDATED') ||
    !recordValid(value.record, id) ||
    value.record.revision !== (create ? 1 : expectedRevision + 1) ||
    JSON.stringify(copyOpportunity(value.record.opportunity)) !== JSON.stringify(opportunity)
  )
    throw new OpportunityRequestError('Invalid or mismatched opportunity save response.');
  return {
    status: value.status as 'CREATED' | 'ALREADY_PRESENT' | 'UPDATED',
    record: copyRecord(value.record),
  };
}
export function emptyOpportunity(): NativeOpportunity {
  return {
    schema_version: 'native-opportunity-1',
    title: '',
    opportunity_type: 'FREELANCE_PROJECT',
    organization: '',
    description: '',
    location: '',
    work_arrangement: null,
    deadline: null,
    source_platform: '',
    source_url: '',
    required_skills: [],
    requirements_description: '',
    source_origin: 'manual_operator_entry',
    verification_status: 'NOT_VERIFIED',
  };
}
