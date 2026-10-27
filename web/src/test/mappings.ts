// Synthetic mock state only. Review/approval statuses simulate operator actions, not human evidence.
import { createHash } from 'node:crypto';
import type {
  MappingFinding,
  MappingOverview,
  MappingRecord,
  ReviewBinding,
} from '../api/capabilityMappingTypes';
import { snapshotExample, snapshotPeriod } from './snapshot';
import { reviewFinding } from './reviews';

export const skillA = snapshotExample.skill_catalog[0]!.skill_id;
export const skillB = snapshotExample.skill_catalog[1]!.skill_id;
export const mappingId = '10000000-0000-0000-0000-000000000001';
export const otherHash = `sha256:${'a'.repeat(64)}`;
export function binding(event: string | null = 'review-1'): ReviewBinding {
  const value = {
    statement: 'Synthetic PostgreSQL requirement.',
    category: 'MUST' as const,
    requirement_type: 'TECHNICAL' as const,
  };
  const canonical = JSON.stringify({
    category: value.category,
    requirement_type: value.requirement_type,
    statement: value.statement,
  });
  return {
    review_event_id: event,
    digest: `sha256:${createHash('sha256').update(canonical).digest('hex')}`,
    value,
  };
}
export function mappingFinding(id = 'finding-1'): MappingFinding {
  return {
    finding_id: id,
    original_requirement: { ...binding().value, statement: 'Original synthetic requirement.' },
    effective_requirement: binding().value,
    review_status: 'CONFIRMED',
    grounding_status: 'VERIFIED_QUOTE',
    source_references: [
      {
        document_id: 'synthetic-doc',
        page_number: 1,
        quote: 'Synthetic PostgreSQL requirement.',
        grounding_status: 'VERIFIED_QUOTE',
      },
    ],
    current_review_binding: binding(),
    mapping_preparation_eligible: true,
    mapping: {
      status: 'NOT_FOUND',
      mapping_id: null,
      revision: null,
      operator: null,
      skill_ids: [],
      unavailable_reasons: ['NO_PERSISTED_MAPPING'],
      approved_review_binding: null,
    },
  };
}
export function mappingOverviewFixture(): MappingOverview {
  const f = mappingFinding();
  return {
    schema_version: 'capability-workflow-overview-1',
    analysis_id: 'analysis-1',
    snapshot_hash: snapshotExample.content_hash,
    analysis_finding_ids: [f.finding_id],
    snapshot: {
      content_hash: snapshotExample.content_hash,
      schema_version: '1.0',
      source_system: 'team-capacity-engine',
      assessment_period: { ...snapshotPeriod },
      skill_catalog: structuredClone(snapshotExample.skill_catalog),
      workforce_member_count: 3,
      source_limitations: [...snapshotExample.source_limitations],
    },
    findings: [f],
    skill_relevance: 'REQUIRES_EXPLICIT_OPERATOR_SELECTION',
  };
}
export function recordFixture(status: 'DRAFT' | 'APPROVED' = 'DRAFT'): MappingRecord {
  return {
    schema_version: 'capability-mapping-record-1',
    matching_contract_version: 'requirement-skill-mapping-1',
    mapping_id: mappingId,
    analysis_id: 'analysis-1',
    finding_id: 'finding-1',
    snapshot_hash: snapshotExample.content_hash,
    operator: 'ALL_OF',
    skill_ids: [skillB],
    revision: status === 'DRAFT' ? 1 : 2,
    status,
    review_binding: binding(),
    rationale: 'Synthetic rationale.',
    operator_alias: 'operator_a',
    created_at: '2026-10-26T10:00:00Z',
    recorded_at: '2026-10-26T10:01:00Z',
    approved_at: status === 'APPROVED' ? '2026-10-26T10:01:00Z' : null,
  };
}
export function persistFixture(overview: MappingOverview, record: MappingRecord) {
  overview.findings.find((f) => f.finding_id === record.finding_id)!.mapping = {
    status: record.status,
    mapping_id: record.mapping_id,
    revision: record.revision,
    operator: 'ALL_OF',
    skill_ids: [...record.skill_ids],
    unavailable_reasons: record.status === 'DRAFT' ? ['MAPPING_NOT_APPROVED'] : [],
    approved_review_binding:
      record.status === 'APPROVED' ? structuredClone(record.review_binding) : null,
  };
}
export function metadataFixture(content_hash = snapshotExample.content_hash) {
  return {
    content_hash,
    schema_version: '1.0',
    source_system: 'team-capacity-engine',
    assessment_period_start: snapshotPeriod.start_date,
    assessment_period_end: snapshotPeriod.end_date,
    imported_at: '2026-10-26T10:00:00Z',
  };
}
export const inventoryFinding = (analysis_run_id = 'analysis-1') => ({
  ...structuredClone(reviewFinding),
  analysis_run_id,
  id: `inventory-${analysis_run_id}`,
});
