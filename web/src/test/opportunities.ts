// Original synthetic external references. No real listing or procurement text.
import { emptyOpportunity, type OpportunityRecord } from '../api/opportunities';
export const opportunityId = '30000000-0000-0000-0000-000000000001';
export function opportunityRecord(): OpportunityRecord {
  return {
    schema_version: 'native-opportunity-record-1',
    opportunity_id: opportunityId,
    revision: 1,
    created_at: '2026-10-30T10:00:00Z',
    updated_at: '2026-10-30T10:00:00Z',
    opportunity: {
      ...emptyOpportunity(),
      title: 'Synthetic Python project',
      organization: 'Synthetic client',
      description: 'Self-authored project example',
      location: 'Synthetic city',
      work_arrangement: 'REMOTE',
      deadline: '2027-03-01',
      source_platform: 'Synthetic platform',
      source_url: 'https://example.invalid/opportunity?ref=manual#details',
      required_skills: ['Python', 'FastAPI'],
      requirements_description: 'Manually supplied, not legally interpreted.',
    },
  };
}
