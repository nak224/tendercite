import type { SnapshotSummary } from '../api/tenderciteSnapshots';
import { snapshotExample, snapshotJson, snapshotPeriod } from './snapshot';

// Preserve non-canonical whitespace to detect accidental parsing/reserialization in the client.
export const snapshotFileText = ` \r\n${snapshotJson}\r\n\t`;
export const snapshotFileBytes = () => new TextEncoder().encode(snapshotFileText).buffer;
export const snapshotFile = () =>
  new File([snapshotFileText], 'synthetic-capability.json', { type: 'application/json' });
export function snapshotSummary(status: SnapshotSummary['status'] = 'VALIDATED'): SnapshotSummary {
  return {
    status,
    schema_version: '1.0',
    source_system: 'team-capacity-engine',
    content_hash: snapshotExample.content_hash,
    assessment_period: { ...snapshotPeriod },
    member_count: snapshotExample.workforce_members.length,
    assignment_count: snapshotExample.workforce_members.reduce((n, m) => n + m.skills.length, 0),
    catalog_count: snapshotExample.skill_catalog.length,
    imported_at: status === 'VALIDATED' ? null : '2026-10-24T12:00:00Z',
  };
}
