import { useState } from 'react';
import { proposeMapping, MappingInputError } from '../api/capabilityMappings';
import type {
  MappingFinding,
  MappingOverview,
  MappingProposal,
} from '../api/capabilityMappingTypes';

export function MappingEditor({
  overview,
  finding,
  disabled,
  create,
  approve,
}: {
  overview: MappingOverview;
  finding: MappingFinding;
  disabled: boolean;
  create: (proposal: MappingProposal) => Promise<void>;
  approve: (rationale: string, alias: string) => Promise<void>;
}) {
  const [skills, setSkills] = useState<string[]>([]);
  const [rationale, setRationale] = useState('');
  const [alias, setAlias] = useState('');
  const [proposal, setProposal] = useState<MappingProposal | null>(null);
  const [confirmed, setConfirmed] = useState(false);
  const [error, setError] = useState('');
  const state = finding.mapping;
  const names = (ids: string[]) =>
    ids.map((id) => {
      const skill = overview.snapshot.skill_catalog.find((s) => s.skill_id === id)!;
      return (
        <li key={id}>
          {skill.name} · {skill.category} · {id}
        </li>
      );
    });
  function edit(change: () => void) {
    if (disabled) return;
    change();
    setProposal(null);
    setConfirmed(false);
    setError('');
  }
  return (
    <section className="panel" aria-label="Selected mapping">
      <h3>{finding.effective_requirement.statement}</h3>
      <p>Finding: {finding.finding_id}</p>
      <p>
        {finding.effective_requirement.category} · {finding.effective_requirement.requirement_type}{' '}
        · Review: {finding.review_status} · Grounding: {finding.grounding_status}
      </p>
      <p>
        Mapping state:{' '}
        {state.status === 'NOT_FOUND' ? 'NOT_FOUND — no persisted mapping' : state.status}
      </p>
      {state.mapping_id && (
        <p>
          Mapping ID: {state.mapping_id} · Revision: {state.revision} · Operator: {state.operator}
        </p>
      )}
      {!!state.skill_ids.length && (
        <>
          <p>Persisted selected skills:</p>
          <ul>{names(state.skill_ids)}</ul>
        </>
      )}
      {!!state.unavailable_reasons.length && (
        <p>Unavailable reasons: {state.unavailable_reasons.join(', ')}</p>
      )}
      <p>
        Current review event: {finding.current_review_binding.review_event_id ?? 'None'} · Digest:{' '}
        {finding.current_review_binding.digest}
      </p>
      {state.status === 'APPROVED' && (
        <p>
          APPROVED records an operator skill association. It is not independently verified
          professional qualification.
        </p>
      )}
      {state.status === 'STALE' && (
        <p>
          This approval is STALE and is not current. Revision/reapproval requires the separate CLI
          workflow; this page cannot reactivate it.
        </p>
      )}
      {state.status === 'REVOKED' && (
        <p>This mapping is REVOKED and cannot be used or reactivated here.</p>
      )}
      {!finding.mapping_preparation_eligible && (
        <p role="note">
          Not eligible for mapping: an accepted current review and VERIFIED_QUOTE grounding are
          required. UNREVIEWED or REJECTED findings cannot be mapped or approved.
        </p>
      )}
      {finding.mapping_preparation_eligible && ['NOT_FOUND', 'DRAFT'].includes(state.status) && (
        <form
          onSubmit={(event) => {
            event.preventDefault();
            if (disabled) return;
            if (state.status === 'DRAFT') {
              if (confirmed) void approve(rationale, alias);
            } else {
              try {
                setProposal(proposeMapping(overview, finding, skills, rationale, alias));
                setError('');
              } catch (failure) {
                setError(
                  failure instanceof MappingInputError
                    ? failure.message
                    : 'Cannot prepare this mapping.',
                );
              }
            }
          }}
        >
          <fieldset disabled={disabled}>
            {state.status === 'NOT_FOUND' && (
              <fieldset>
                <legend>Select catalog skills — ALL_OF</legend>
                <p>
                  Explicit selection only. No skills are inferred. All selected UUIDs belong to this
                  one snapshot.
                </p>
                {overview.snapshot.skill_catalog.map((skill) => (
                  <label key={skill.skill_id}>
                    <input
                      type="checkbox"
                      checked={skills.includes(skill.skill_id)}
                      disabled={!skills.includes(skill.skill_id) && skills.length >= 100}
                      onChange={(e) =>
                        edit(() =>
                          setSkills(
                            e.target.checked
                              ? [...skills, skill.skill_id]
                              : skills.filter((id) => id !== skill.skill_id),
                          ),
                        )
                      }
                    />
                    {skill.name} · {skill.category} · {skill.skill_id}
                  </label>
                ))}
                {!overview.snapshot.skill_catalog.length && <p>No catalog skills are available.</p>}
              </fieldset>
            )}
            <label>
              {state.status === 'DRAFT' ? 'Approval rationale' : 'Mapping rationale'}
              <textarea
                required
                maxLength={2000}
                value={rationale}
                onChange={(e) => edit(() => setRationale(e.target.value))}
              />
            </label>
            <label>
              Non-personal operator alias (optional)
              <input
                maxLength={64}
                pattern="[a-zA-Z0-9_-]{1,64}"
                value={alias}
                onChange={(e) => edit(() => setAlias(e.target.value))}
              />
            </label>
            {state.status === 'DRAFT' ? (
              <>
                <p>
                  Approval uses the displayed revision and current review event/digest. The backend
                  also checks that the stored draft is bound to that same review.
                </p>
                <label>
                  <input
                    type="checkbox"
                    checked={confirmed}
                    onChange={(e) => setConfirmed(e.target.checked)}
                  />
                  I explicitly approve this ALL_OF skill association. This does not verify
                  professional qualification, certification, bidder eligibility or capacity.
                </label>
                <button type="submit" disabled={!confirmed}>
                  APPROVE mapping
                </button>
              </>
            ) : (
              <button type="submit">Review proposed mapping</button>
            )}
          </fieldset>
        </form>
      )}
      {error && <p role="alert">{error}</p>}
      {proposal && state.status === 'NOT_FOUND' && (
        <section aria-label="Proposed mapping">
          <h4>Proposed ALL_OF mapping — not saved</h4>
          <p>
            Analysis: {proposal.selection.analysis_id} · Snapshot:{' '}
            {proposal.selection.snapshot_hash}
          </p>
          <p>Reviewed requirement: {proposal.binding.value.statement}</p>
          <ul>{names(proposal.selection.skill_ids)}</ul>
          <p>Rationale: {proposal.selection.rationale}</p>
          <p>Operator alias: {proposal.selection.operator_alias ?? 'None'}</p>
          <p>
            Draft creation captures the server's current review. If it changed since this preview,
            inspect the refreshed result before any approval.
          </p>
          <button type="button" disabled={disabled} onClick={() => void create(proposal)}>
            Create DRAFT
          </button>
        </section>
      )}
    </section>
  );
}
