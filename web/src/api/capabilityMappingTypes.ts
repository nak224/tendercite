import type { TenderFinding } from './tendercite';
import type { ReviewedValue } from './tenderciteReviews';
import type { Period } from '../utils/period';

export interface MappingScope {
  analysis_id: string;
  snapshot_hash: string;
}
export interface ReviewBinding {
  review_event_id: string | null;
  digest: string;
  value: ReviewedValue;
}
export interface CatalogSkill {
  skill_id: string;
  name: string;
  category: string;
}
export interface SnapshotMetadata {
  content_hash: string;
  schema_version: '1.0';
  source_system: 'team-capacity-engine';
  assessment_period_start: string;
  assessment_period_end: string;
  imported_at: string;
}
export type MappingStatus = 'NOT_FOUND' | 'DRAFT' | 'APPROVED' | 'STALE' | 'REVOKED';
export interface MappingState {
  status: MappingStatus;
  mapping_id: string | null;
  revision: number | null;
  operator: 'ALL_OF' | null;
  skill_ids: string[];
  unavailable_reasons: string[];
  approved_review_binding: ReviewBinding | null;
}
export interface MappingFinding {
  finding_id: string;
  original_requirement: ReviewedValue;
  effective_requirement: ReviewedValue;
  review_status: string;
  grounding_status: string;
  source_references: TenderFinding['evidence'];
  current_review_binding: ReviewBinding;
  mapping_preparation_eligible: boolean;
  mapping: MappingState;
}
export interface MappingOverview extends MappingScope {
  schema_version: 'capability-workflow-overview-1';
  analysis_finding_ids: string[];
  findings: MappingFinding[];
  snapshot: {
    content_hash: string;
    schema_version: '1.0';
    source_system: 'team-capacity-engine';
    assessment_period: Period;
    skill_catalog: CatalogSkill[];
    workforce_member_count: number;
    source_limitations: string[];
  };
  skill_relevance: 'REQUIRES_EXPLICIT_OPERATOR_SELECTION';
}
export interface MappingSelection extends MappingScope {
  schema_version: 'capability-workflow-mapping-selection-1';
  finding_id: string;
  operator: 'ALL_OF';
  skill_ids: string[];
  rationale: string;
  operator_alias: string | null;
}
export interface MappingProposal {
  selection: MappingSelection;
  binding: ReviewBinding;
}
export interface MappingApproval {
  schema_version: 'capability-mapping-approval-1';
  mapping_id: string;
  expected_revision: number;
  expected_review_event_id: string;
  expected_review_digest: string;
  rationale: string;
  operator_alias: string | null;
}
export interface MappingRecord extends MappingScope {
  schema_version: 'capability-mapping-record-1';
  matching_contract_version: 'requirement-skill-mapping-1';
  mapping_id: string;
  finding_id: string;
  operator: 'ALL_OF';
  skill_ids: string[];
  revision: number;
  status: 'DRAFT' | 'APPROVED' | 'REVOKED';
  review_binding: ReviewBinding;
  rationale: string;
  operator_alias: string | null;
  created_at: string;
  recorded_at: string;
  approved_at: string | null;
}
