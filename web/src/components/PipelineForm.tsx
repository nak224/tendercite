import { useState } from 'react';
import { pipelinePriorities, pipelineStages, type PipelineValues } from '../api/pipeline';

export function PipelineForm({
  initial,
  busy,
  blocked,
  onDirty,
  save,
}: {
  initial: PipelineValues;
  busy: boolean;
  blocked: boolean;
  onDirty: () => void;
  save: (value: PipelineValues) => Promise<void>;
}) {
  const [values, setValues] = useState(initial);
  function edit(patch: Partial<PipelineValues>) {
    onDirty();
    setValues((v) => ({ ...v, ...patch }));
  }
  return (
    <form
      onSubmit={(event) => {
        event.preventDefault();
        void save(values);
      }}
    >
      <fieldset disabled={busy}>
        <legend>Manual pipeline progress</legend>
        <div className="form-grid">
          <label>
            Stage
            <select
              value={values.stage}
              onChange={(e) => edit({ stage: e.target.value as PipelineValues['stage'] })}
            >
              {pipelineStages.map((s) => (
                <option key={s}>{s}</option>
              ))}
            </select>
          </label>
          <label>
            Priority
            <select
              value={values.priority}
              onChange={(e) => edit({ priority: e.target.value as PipelineValues['priority'] })}
            >
              {pipelinePriorities.map((s) => (
                <option key={s}>{s}</option>
              ))}
            </select>
          </label>
        </div>
        <label>
          Notes (optional, maximum 4000 characters)
          <textarea
            maxLength={4000}
            value={values.notes}
            onChange={(e) => edit({ notes: e.target.value })}
          />
        </label>
        <label>
          Next action (optional, maximum 1000 characters)
          <textarea
            maxLength={1000}
            value={values.next_action}
            onChange={(e) => edit({ next_action: e.target.value })}
          />
        </label>
        <label>
          Next-action due date (optional, date only)
          <input
            type="date"
            value={values.next_action_due_date ?? ''}
            onChange={(e) => edit({ next_action_due_date: e.target.value || null })}
          />
        </label>
        <p>
          The next-action date is your follow-up date, separate from the opportunity's submission
          deadline. No reminder is sent.
        </p>
        <button type="submit" disabled={blocked}>
          Save Changes
        </button>
      </fieldset>
    </form>
  );
}
