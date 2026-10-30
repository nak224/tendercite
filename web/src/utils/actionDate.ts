import type { PipelineStage } from '../api/pipeline';

// Compare calendar dates on the operator's device, never invent a deadline time.
export function localCalendarDate(now: Date): string {
  return [
    String(now.getFullYear()).padStart(4, '0'),
    String(now.getMonth() + 1).padStart(2, '0'),
    String(now.getDate()).padStart(2, '0'),
  ].join('-');
}
export function actionDateLabel(due: string | null, stage: PipelineStage, today: string): string {
  if (!due) return 'No action date';
  if (['WON', 'LOST', 'ARCHIVED'].includes(stage)) return 'Recorded action date';
  if (due < today) return 'Overdue action date';
  if (due === today) return 'Action date today';
  return 'Upcoming action date';
}
