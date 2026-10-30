import { useState } from 'react';
import type { NativeProfile, ProfileType, WorkArrangement } from '../api/profiles';

export function ProfileForm({
  initial,
  disabled,
  save,
}: {
  initial: NativeProfile;
  disabled: boolean;
  save: (profile: NativeProfile) => Promise<void>;
}) {
  const [profile, setProfile] = useState(initial);
  const [keywords, setKeywords] = useState(initial.opportunity_keywords.join('\n'));
  function edit(patch: Partial<NativeProfile>) {
    setProfile((p) => ({ ...p, ...patch }));
  }
  return (
    <form
      onSubmit={(event) => {
        event.preventDefault();
        void save({
          ...profile,
          opportunity_keywords: keywords
            .split('\n')
            .map((s) => s.trim())
            .filter(Boolean),
        });
      }}
    >
      <fieldset disabled={disabled}>
        <legend>Profile information</legend>
        <div className="form-grid">
          <label>
            Profile name
            <input
              required
              maxLength={120}
              value={profile.name}
              onChange={(e) => edit({ name: e.target.value })}
            />
          </label>
          <label>
            Profile type
            <select
              value={profile.profile_type}
              onChange={(e) => edit({ profile_type: e.target.value as ProfileType })}
            >
              <option value="FREELANCER">Freelancer</option>
              <option value="COMPANY">Company</option>
            </select>
          </label>
          <label>
            Location (optional)
            <input
              maxLength={120}
              value={profile.location}
              onChange={(e) => edit({ location: e.target.value })}
            />
          </label>
          <label>
            Preferred work arrangement
            <select
              value={profile.work_arrangement ?? ''}
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
        </div>
        <label>
          Headline or short description
          <textarea
            maxLength={300}
            value={profile.headline}
            onChange={(e) => edit({ headline: e.target.value })}
          />
        </label>
        <fieldset>
          <legend>Declared skills ({profile.skills.length}/100)</legend>
          {profile.skills.map((skill, index) => (
            <div className="form-grid" key={skill.skill_id}>
              <label>
                Skill {index + 1} name
                <input
                  required
                  maxLength={100}
                  value={skill.name}
                  onChange={(e) => {
                    const name = e.target.value;
                    edit({
                      skills: profile.skills.map((s) =>
                        s.skill_id === skill.skill_id ? { ...s, name } : s,
                      ),
                    });
                  }}
                />
              </label>
              <button
                type="button"
                onClick={() =>
                  edit({ skills: profile.skills.filter((s) => s.skill_id !== skill.skill_id) })
                }
              >
                Remove skill {index + 1}
              </button>
            </div>
          ))}
          <button
            type="button"
            disabled={profile.skills.length >= 100}
            onClick={() =>
              edit({
                skills: [
                  ...profile.skills,
                  {
                    skill_id: crypto.randomUUID(),
                    name: '',
                    source_origin: 'manual_operator_entry',
                    verification_status: 'NOT_VERIFIED',
                  },
                ],
              })
            }
          >
            Add skill
          </button>
          <p>
            Skills are manually declared and NOT_VERIFIED. Internal IDs are assigned automatically;
            no external catalog, years of experience or certificates are required.
          </p>
        </fieldset>
        <label>
          Opportunity keywords (optional, one per line)
          <textarea
            maxLength={2000}
            value={keywords}
            onChange={(e) => setKeywords(e.target.value)}
          />
        </label>
        <p>
          Up to 20 keywords, 80 characters each. These are preferences only; no recommendations or
          matching run.
        </p>
        <button type="submit">Save profile</button>
      </fieldset>
    </form>
  );
}
