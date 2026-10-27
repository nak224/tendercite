import type { MappingFinding, MappingOverview, MappingScope } from './capabilityMappingTypes';

export interface AssessmentSelection extends MappingScope {
  schema_version: 'capability-workflow-assessment-selection-1';
  finding_ids: string[];
}
export const coverageValues = [
  'ALL_REQUIRED_SKILLS_RECORDED',
  'SOME_REQUIRED_SKILLS_RECORDED',
  'NO_REQUIRED_SKILLS_RECORDED',
  'NOT_EVALUATED',
] as const;
export type RecordedCoverage = (typeof coverageValues)[number];
export interface AssessedFinding {
  finding: MappingFinding;
  coverage: RecordedCoverage;
  individual_counts: {
    all_required_recorded: number;
    some_required_recorded: number;
    no_required_recorded: number;
  };
  individual_coverage: {
    person_id: string;
    matched_skill_ids: string[];
    missing_assignment_ids: string[];
    coverage: RecordedCoverage;
  }[];
  non_evaluation_reasons: string[];
  limitations: {
    schema_version: 'capability-evidence-limitation-1';
    code: string;
    explanation: string;
  }[];
  qualification: 'NOT_VERIFIED';
  capacity_evaluation: 'NOT_EVALUATED';
  reviewed_meaning_verification: 'NOT_ASSESSED';
}
export interface AssessmentReport extends MappingScope {
  schema_version: 'capability-workflow-report-1';
  matching_contract_version: 'capability-matching-result-1';
  algorithm_version: 'explicit-uuid-membership-1';
  purpose: 'ASSESSMENT_SUPPORT_ONLY';
  snapshot: MappingOverview['snapshot'];
  summary: {
    selected_finding_count: number;
    current_approved: number;
    drafts: number;
    stale: number;
    revoked: number;
    without_mapping: number;
    not_eligible_for_matching: number;
  };
  findings: AssessedFinding[];
  evidence_boundaries: string[];
  qualification: 'NOT_VERIFIED';
  capacity_evaluation: 'NOT_EVALUATED';
}
