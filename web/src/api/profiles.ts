import { tenderGet, tenderProfileWrite } from './tendercite';
import { object, uuid, count, timestamp, nonblank } from './capabilityMappingValidation';

export type ProfileType = 'FREELANCER' | 'COMPANY';
export type WorkArrangement = 'REMOTE' | 'HYBRID' | 'ON_SITE' | null;
export interface ProfileSkill {
  skill_id: string;
  name: string;
  source_origin: 'manual_operator_entry';
  verification_status: 'NOT_VERIFIED';
}
export interface NativeProfile {
  schema_version: 'native-capability-profile-1';
  name: string;
  profile_type: ProfileType;
  headline: string;
  location: string;
  work_arrangement: WorkArrangement;
  skills: ProfileSkill[];
  opportunity_keywords: string[];
  source_origin: 'manual_operator_entry';
  qualification: 'NOT_VERIFIED';
}
export interface ProfileRecord {
  schema_version: 'native-profile-record-1';
  profile_id: string;
  revision: number;
  created_at: string;
  updated_at: string;
  profile: NativeProfile;
}
export type ProfileMetadata = Pick<ProfileRecord, 'profile_id' | 'revision' | 'updated_at'> &
  Pick<NativeProfile, 'name' | 'profile_type'>;
export interface ProfileListing {
  schema_version: 'native-profile-listing-1';
  profiles: ProfileMetadata[];
  next_offset: number | null;
}
export class ProfileInputError extends Error {}
export class ProfileRequestError extends Error {
  readonly code: string;
  constructor(message: string, code = '') {
    super(message);
    this.code = code;
  }
}
const safeCodes = new Set([
  'PROFILES_API_DISABLED',
  'INVALID_PROFILE_INPUT',
  'PROFILE_NOT_FOUND',
  'PROFILE_ID_CONFLICT',
  'PROFILE_REVISION_CONFLICT',
  'PROFILE_STORAGE_INTEGRITY',
  'DATABASE_FAILED',
  'PROFILE_REQUEST_TOO_LARGE',
  'PROFILE_OPERATION_FAILED',
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
    /* No raw backend/proxy errors, input values or field names. */
  }
  return new ProfileRequestError(
    `Profile request failed (HTTP ${response.status})${code ? `: ${code}` : ''}.`,
    code,
  );
}
const labelKey = (name: string) =>
  name.normalize('NFKC').toLowerCase().replace(/\s+/gu, ' ').trim();
const unique = (values: string[]) => new Set(values).size === values.length;
const text = (v: unknown, max: number): v is string =>
  typeof v === 'string' && [...v].length <= max;
const profileType = (v: unknown): v is ProfileType => v === 'FREELANCER' || v === 'COMPANY';
function profileValid(p: unknown): p is NativeProfile {
  return (
    object(p) &&
    p.schema_version === 'native-capability-profile-1' &&
    nonblank(p.name, 120) &&
    profileType(p.profile_type) &&
    text(p.headline, 300) &&
    text(p.location, 120) &&
    [null, 'REMOTE', 'HYBRID', 'ON_SITE'].includes(p.work_arrangement as string | null) &&
    p.source_origin === 'manual_operator_entry' &&
    p.qualification === 'NOT_VERIFIED' &&
    Array.isArray(p.skills) &&
    p.skills.length <= 100 &&
    p.skills.every(
      (s) =>
        object(s) &&
        uuid(s.skill_id) &&
        nonblank(s.name, 100) &&
        s.source_origin === 'manual_operator_entry' &&
        s.verification_status === 'NOT_VERIFIED',
    ) &&
    unique(p.skills.map((s) => s.skill_id)) &&
    unique(p.skills.map((s) => labelKey(s.name))) &&
    Array.isArray(p.opportunity_keywords) &&
    p.opportunity_keywords.length <= 20 &&
    p.opportunity_keywords.every((k) => nonblank(k, 80)) &&
    unique(p.opportunity_keywords.map(labelKey))
  );
}
function copyProfile(p: NativeProfile): NativeProfile {
  return {
    schema_version: p.schema_version,
    name: p.name,
    profile_type: p.profile_type,
    headline: p.headline,
    location: p.location,
    work_arrangement: p.work_arrangement,
    source_origin: p.source_origin,
    qualification: p.qualification,
    skills: p.skills.map((s) => ({
      skill_id: s.skill_id,
      name: s.name,
      source_origin: s.source_origin,
      verification_status: s.verification_status,
    })),
    opportunity_keywords: [...p.opportunity_keywords],
  };
}
export function profileInput(p: NativeProfile): NativeProfile {
  const normalized = {
    ...p,
    name: p.name.trim(),
    headline: p.headline.trim(),
    location: p.location.trim(),
    skills: p.skills.map((s) => ({ ...s, name: s.name.trim() })),
    opportunity_keywords: p.opportunity_keywords.map((s) => s.trim()),
  };
  if (!profileValid(normalized))
    throw new ProfileInputError(
      'Enter a profile name (1–120 characters), valid type, at most 100 unique named skills (1–100 characters each) and 20 unique keywords (1–80 each). Headline: maximum 300; location: maximum 120.',
    );
  return copyProfile(normalized);
}
function recordValid(value: unknown, id: string): value is ProfileRecord {
  return (
    object(value) &&
    value.schema_version === 'native-profile-record-1' &&
    value.profile_id === id &&
    uuid(value.profile_id) &&
    count(value.revision) &&
    value.revision >= 1 &&
    value.revision <= 2147483647 &&
    timestamp(value.created_at) &&
    timestamp(value.updated_at) &&
    Date.parse(value.created_at) <= Date.parse(value.updated_at) &&
    profileValid(value.profile)
  );
}
function copyRecord(r: ProfileRecord): ProfileRecord {
  return {
    schema_version: r.schema_version,
    profile_id: r.profile_id,
    revision: r.revision,
    created_at: r.created_at,
    updated_at: r.updated_at,
    profile: copyProfile(r.profile),
  };
}
async function get(path: string, signal: AbortSignal): Promise<unknown> {
  try {
    return await tenderGet(path, signal, responseError);
  } catch (error) {
    if (signal.aborted || error instanceof ProfileRequestError) throw error;
    throw new ProfileRequestError('Profile data unavailable or invalid. Reload explicitly.');
  }
}
export async function listProfiles(offset: number, signal: AbortSignal): Promise<ProfileListing> {
  if (!count(offset) || offset > 1000000) throw new ProfileInputError('Invalid profile page.');
  const result = await get(`profiles?limit=50&offset=${offset}`, signal);
  if (
    !object(result) ||
    result.schema_version !== 'native-profile-listing-1' ||
    !Array.isArray(result.profiles) ||
    result.profiles.length > 50 ||
    !(result.next_offset === null || result.next_offset === offset + 50) ||
    !result.profiles.every(
      (p) =>
        object(p) &&
        uuid(p.profile_id) &&
        count(p.revision) &&
        p.revision > 0 &&
        nonblank(p.name, 120) &&
        profileType(p.profile_type) &&
        timestamp(p.updated_at),
    ) ||
    !unique(result.profiles.map((p) => p.profile_id))
  )
    throw new ProfileRequestError('Invalid profile listing.');
  return {
    schema_version: result.schema_version,
    next_offset: result.next_offset as number | null,
    profiles: result.profiles.map((p) => ({
      profile_id: p.profile_id,
      revision: p.revision,
      name: p.name,
      profile_type: p.profile_type,
      updated_at: p.updated_at,
    })),
  };
}
export async function getProfile(id: string, signal: AbortSignal): Promise<ProfileRecord> {
  if (!uuid(id)) throw new ProfileInputError('Select an existing profile.');
  const result = await get(`profiles/${encodeURIComponent(id)}`, signal);
  if (!recordValid(result, id))
    throw new ProfileRequestError('Invalid or mismatched profile response.');
  return copyRecord(result);
}
export async function saveProfile(
  id: string,
  input: NativeProfile,
  expectedRevision?: number,
): Promise<{ status: 'CREATED' | 'ALREADY_PRESENT' | 'UPDATED'; record: ProfileRecord }> {
  const profile = profileInput(input);
  if (
    !uuid(id) ||
    (expectedRevision !== undefined &&
      (!count(expectedRevision) || expectedRevision < 1 || expectedRevision > 2147483647))
  )
    throw new ProfileInputError('Invalid profile identity or revision.');
  const create = expectedRevision === undefined;
  const body = JSON.stringify(
    create
      ? { schema_version: 'native-profile-create-1', profile_id: id, profile }
      : { schema_version: 'native-profile-update-1', expected_revision: expectedRevision, profile },
  );
  if (new TextEncoder().encode(body).byteLength > 65536)
    throw new ProfileInputError('Profile request exceeds the 65,536-byte limit.');
  let result: unknown;
  try {
    result = await tenderProfileWrite(create ? 'create' : 'update', id, body, responseError);
  } catch (error) {
    if (error instanceof ProfileRequestError) throw error;
    throw new ProfileRequestError(
      'Profile save response unavailable or invalid. No automatic retry was made.',
    );
  }
  if (
    !object(result) ||
    result.schema_version !== 'native-profile-save-result-1' ||
    !(create
      ? result.status === 'CREATED' || result.status === 'ALREADY_PRESENT'
      : result.status === 'UPDATED') ||
    !recordValid(result.record, id) ||
    result.record.revision !== (create ? 1 : expectedRevision + 1) ||
    JSON.stringify(copyProfile(result.record.profile)) !== JSON.stringify(profile)
  )
    throw new ProfileRequestError('Invalid or mismatched profile save response.');
  return {
    status: result.status as 'CREATED' | 'ALREADY_PRESENT' | 'UPDATED',
    record: copyRecord(result.record),
  };
}
export function emptyProfile(): NativeProfile {
  return {
    schema_version: 'native-capability-profile-1',
    name: '',
    profile_type: 'FREELANCER',
    headline: '',
    location: '',
    work_arrangement: null,
    skills: [],
    opportunity_keywords: [],
    source_origin: 'manual_operator_entry',
    qualification: 'NOT_VERIFIED',
  };
}
