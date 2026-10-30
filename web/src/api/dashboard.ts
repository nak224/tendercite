// Reuse the existing bounded read API; retain no pipeline notes or full records on the dashboard.
import { listPipeline, type PipelineStage, type PipelinePriority } from './pipeline';

export interface PipelinePreview {
  pipeline_id: string;
  opportunity_id: string;
  title: string;
  stage: PipelineStage;
  priority: PipelinePriority;
  next_action: string;
  next_action_due_date: string | null;
}
export async function dashboardPipeline(signal: AbortSignal) {
  const listing = await listPipeline(0, '', signal);
  return {
    next_offset: listing.next_offset,
    entries: listing.entries.map((e): PipelinePreview => ({
      pipeline_id: e.record.pipeline_id,
      opportunity_id: e.record.opportunity_id,
      title: e.opportunity.title,
      stage: e.record.values.stage,
      priority: e.record.values.priority,
      next_action: e.record.values.next_action,
      next_action_due_date: e.record.values.next_action_due_date,
    })),
  };
}
