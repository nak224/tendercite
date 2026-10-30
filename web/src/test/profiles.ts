// Entirely synthetic native declarations, independent of workforce fixtures.
import { emptyProfile, type ProfileRecord } from '../api/profiles';
export const profileId = '20000000-0000-0000-0000-000000000001';
export const skillId = '20000000-0000-0000-0000-000000000002';
export function profileRecord(): ProfileRecord {
  return {
    schema_version: 'native-profile-record-1',
    profile_id: profileId,
    revision: 1,
    created_at: '2026-10-27T10:00:00Z',
    updated_at: '2026-10-27T10:00:00Z',
    profile: {
      ...emptyProfile(),
      name: 'Synthetic freelancer',
      headline: 'Synthetic software services',
      skills: [
        {
          skill_id: skillId,
          name: 'Python',
          source_origin: 'manual_operator_entry',
          verification_status: 'NOT_VERIFIED',
        },
      ],
      opportunity_keywords: ['Consulting'],
    },
  };
}
