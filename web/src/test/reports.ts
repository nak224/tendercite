// Self-authored simulated report data. No genuine human review or qualification evidence.
import type { AssessmentReport, AssessedFinding } from '../api/capabilityReportTypes';
import { mappingOverviewFixture, persistFixture, recordFixture, skillA, skillB } from './mappings';
export function reportRow(id = 'finding-1'): AssessedFinding {
  const overview = mappingOverviewFixture();
  const record = recordFixture('APPROVED');
  record.skill_ids = [skillA, skillB];
  persistFixture(overview, record);
  overview.findings[0]!.finding_id = id;
  return {
    finding: overview.findings[0]!,
    coverage: 'ALL_REQUIRED_SKILLS_RECORDED',
    individual_counts: {
      all_required_recorded: 1,
      some_required_recorded: 1,
      no_required_recorded: 1,
    },
    individual_coverage: [
      {
        person_id: '00000000-0000-0000-0000-000000000001',
        matched_skill_ids: [skillA, skillB],
        missing_assignment_ids: [],
        coverage: 'ALL_REQUIRED_SKILLS_RECORDED',
      },
      {
        person_id: '00000000-0000-0000-0000-000000000002',
        matched_skill_ids: [skillB],
        missing_assignment_ids: [skillA],
        coverage: 'SOME_REQUIRED_SKILLS_RECORDED',
      },
      {
        person_id: '00000000-0000-0000-0000-000000000003',
        matched_skill_ids: [],
        missing_assignment_ids: [skillA, skillB],
        coverage: 'NO_REQUIRED_SKILLS_RECORDED',
      },
    ],
    non_evaluation_reasons: [],
    limitations: [
      {
        schema_version: 'capability-evidence-limitation-1',
        code: 'PROFICIENCY_NOT_EXPERIENCE_DURATION',
        explanation: 'Proficiency is not verified years of experience.',
      },
    ],
    qualification: 'NOT_VERIFIED',
    capacity_evaluation: 'NOT_EVALUATED',
    reviewed_meaning_verification: 'NOT_ASSESSED',
  };
}
export function reportFixture(ids = ['finding-1']): AssessmentReport {
  return {
    schema_version: 'capability-workflow-report-1',
    analysis_id: 'analysis-1',
    snapshot_hash: mappingOverviewFixture().snapshot_hash,
    matching_contract_version: 'capability-matching-result-1',
    algorithm_version: 'explicit-uuid-membership-1',
    purpose: 'ASSESSMENT_SUPPORT_ONLY',
    snapshot: mappingOverviewFixture().snapshot,
    summary: {
      selected_finding_count: ids.length,
      current_approved: ids.length,
      drafts: 0,
      stale: 0,
      revoked: 0,
      without_mapping: 0,
      not_eligible_for_matching: 0,
    },
    findings: ids.map(reportRow),
    evidence_boundaries: [
      'Source occurrence is not interpretation correctness.',
      'Internal assignments are not independent qualification evidence.',
    ],
    qualification: 'NOT_VERIFIED',
    capacity_evaluation: 'NOT_EVALUATED',
  };
}
