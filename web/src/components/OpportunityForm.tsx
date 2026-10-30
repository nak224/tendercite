import { useState } from 'react';
import type { NativeOpportunity, OpportunityType, WorkArrangement } from '../api/opportunities';

export function OpportunityForm({
  initial,
  disabled,
  save,
  onDirty,
}: {
  initial: NativeOpportunity;
  disabled: boolean;
  save: (value: NativeOpportunity) => Promise<void>;
  onDirty: () => void;
}) {
  const [value, setValue] = useState(initial);
  function edit(patch: Partial<NativeOpportunity>) {
    onDirty();
    setValue((v) => ({ ...v, ...patch }));
  }
  return (
    <form
      onSubmit={(event) => {
        event.preventDefault();
        void save(value);
      }}
    >
      <fieldset disabled={disabled}>
        <legend>Opportunity information</legend>
        <div className="form-grid">
          <label>
            Opportunity title
            <input
              required
              maxLength={200}
              value={value.title}
              onChange={(e) => edit({ title: e.target.value })}
            />
          </label>
          <label>
            Opportunity type
            <select
              value={value.opportunity_type}
              onChange={(e) => edit({ opportunity_type: e.target.value as OpportunityType })}
            >
              <option value="FREELANCE_PROJECT">Freelance project</option>
              <option value="PUBLIC_TENDER">Public tender</option>
              <option value="OTHER">Other</option>
            </select>
          </label>
          <label>
            Organization or client (optional)
            <input
              maxLength={200}
              value={value.organization}
              onChange={(e) => edit({ organization: e.target.value })}
            />
          </label>
          <label>
            Location (optional)
            <input
              maxLength={120}
              value={value.location}
              onChange={(e) => edit({ location: e.target.value })}
            />
          </label>
          <label>
            Work arrangement
            <select
              value={value.work_arrangement ?? ''}
              onChange={(e) =>
                edit({ work_arrangement: (e.target.value || null) as WorkArrangement })
              }
            >
              <option value="">Not specified</option>
              <option value="REMOTE">Remote</option>
              <option value="HYBRID">Hybrid</option>
              <option value="ON_SITE">On-site</option>
            </select>
          </label>
          <label>
            Application/submission deadline (date only, optional)
            <input
              type="date"
              min="0001-01-01"
              max="9999-12-31"
              value={value.deadline ?? ''}
              onChange={(e) => edit({ deadline: e.target.value || null })}
            />
          </label>
        </div>
        <label>
          Short description (optional)
          <textarea
            maxLength={2000}
            value={value.description}
            onChange={(e) => edit({ description: e.target.value })}
          />
        </label>
        <div className="form-grid">
          <label>
            Source platform or organization (optional)
            <input
              maxLength={200}
              value={value.source_platform}
              onChange={(e) => edit({ source_platform: e.target.value })}
            />
          </label>
          <label>
            Original opportunity URL (optional)
            <input
              type="url"
              maxLength={2048}
              value={value.source_url}
              onChange={(e) => edit({ source_url: e.target.value })}
            />
          </label>
        </div>
        <p>
          The source URL is a reference only. TenderCite does not fetch or preview it. A date-only
          deadline does not establish its exact time or time zone.
        </p>
        <fieldset>
          <legend>Required or desired skill labels ({value.required_skills.length}/100)</legend>
          {value.required_skills.map((skill, index) => (
            <div className="form-grid" key={index}>
              <label>
                Skill {index + 1} name
                <input
                  required
                  maxLength={100}
                  value={skill}
                  onChange={(e) => {
                    const name = e.target.value;
                    edit({
                      required_skills: value.required_skills.map((s, i) =>
                        i === index ? name : s,
                      ),
                    });
                  }}
                />
              </label>
              <button
                type="button"
                onClick={() =>
                  edit({ required_skills: value.required_skills.filter((_, i) => i !== index) })
                }
              >
                Remove skill {index + 1}
              </button>
            </div>
          ))}
          <button
            type="button"
            disabled={value.required_skills.length >= 100}
            onClick={() => edit({ required_skills: [...value.required_skills, ''] })}
          >
            Add required skill
          </button>
        </fieldset>
        <label>
          Requirements description (optional)
          <textarea
            maxLength={2000}
            value={value.requirements_description}
            onChange={(e) => edit({ requirements_description: e.target.value })}
          />
        </label>
        <p>
          Skill labels and requirements are manual declarations. Their legal meaning, mandatory
          status and correctness are not verified; no matching runs.
        </p>
        <button type="submit">Save Opportunity</button>
      </fieldset>
    </form>
  );
}
