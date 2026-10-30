// Original synthetic tracking declarations, independent of external platforms.
import { opportunityMetadata } from './nativeMatching';
import { defaultPipelineValues, type PipelineEntry } from '../api/pipeline';
export const pipelineId = '40000000-0000-0000-0000-000000000001';
export function pipelineEntry(): PipelineEntry {
  return {
    schema_version: 'native-pipeline-entry-1',
    record: {
      schema_version: 'native-pipeline-record-1',
      pipeline_id: pipelineId,
      opportunity_id: opportunityMetadata().opportunity_id,
      revision: 1,
      created_at: '2026-10-30T12:00:00Z',
      updated_at: '2026-10-30T12:00:00Z',
      values: defaultPipelineValues(),
    },
    opportunity: opportunityMetadata(),
  };
}
