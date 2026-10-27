import { useState } from 'react';
import { Link } from 'react-router-dom';
import type { AssessmentReport, AssessedFinding } from '../api/capabilityReportTypes';

const summaryLabels = {
  selected_finding_count: 'Selected requirements',
  current_approved: 'Current approved mappings',
  drafts: 'Draft mappings',
  stale: 'Stale mappings',
  revoked: 'Revoked mappings',
  without_mapping: 'Without mappings',
  not_eligible_for_matching: 'Not evaluated',
} as const;

function IndividualCoverage({
  row,
  skills,
}: {
  row: AssessedFinding;
  skills: (ids: string[]) => string;
}) {
  const [open, setOpen] = useState(false);
  return (
    <details onToggle={(event) => setOpen(event.currentTarget.open)}>
      <summary>Individual recorded-skill coverage ({row.individual_coverage.length})</summary>
      {open && (
        <ul>
          {row.individual_coverage.map((person) => (
            <li key={person.person_id}>
              <p>
                Pseudonymous person ID: {person.person_id} · {person.coverage}
              </p>
              <p>Recorded required skills: {skills(person.matched_skill_ids) || 'None recorded'}</p>
              <p>Missing recorded assignments: {skills(person.missing_assignment_ids) || 'None'}</p>
            </li>
          ))}
        </ul>
      )}
    </details>
  );
}

export function CapabilityReportResults({ report }: { report: AssessmentReport }) {
  const skills = (ids: string[]) =>
    ids
      .map((id) => `${report.snapshot.skill_catalog.find((s) => s.skill_id === id)!.name} (${id})`)
      .join(', ');
  return (
    <section aria-label="Capability matching report">
      <div className="panel">
        <h2>Capability matching report</h2>
        <p>Analysis: {report.analysis_id}</p>
        <p className="source-page-text">Snapshot: {report.snapshot_hash}</p>
        <p>
          Assessment period: {report.snapshot.assessment_period.start_date} through{' '}
          {report.snapshot.assessment_period.end_date}
        </p>
        <p>
          {report.schema_version} · {report.algorithm_version} · {report.purpose}
        </p>
        <ul aria-label="Report summary">
          {Object.entries(summaryLabels).map(([key, label]) => (
            <li key={key}>
              {label}: {report.summary[key as keyof typeof summaryLabels]}
            </li>
          ))}
        </ul>
        <p>
          Qualification: {report.qualification} · Capacity: {report.capacity_evaluation}
        </p>
        <p>Point-in-time report. Run again after reviews or mappings change; no report is saved.</p>
        <ul>
          {report.evidence_boundaries.map((text, i) => (
            <li key={i}>{text}</li>
          ))}
        </ul>
        <details>
          <summary>Snapshot source limitations</summary>
          <ul>
            {report.snapshot.source_limitations.map((text, i) => (
              <li key={i}>{text}</li>
            ))}
          </ul>
        </details>
      </div>
      {!report.findings.length && (
        <p className="empty">Empty report: no requirements were selected or evaluated.</p>
      )}
      {report.findings.map((row) => (
        <article
          className="panel"
          key={row.finding.finding_id}
          aria-label={`Requirement ${row.finding.finding_id}`}
        >
          <h3>{row.finding.effective_requirement.statement}</h3>
          <Link to={`/tenders/findings/${encodeURIComponent(row.finding.finding_id)}`}>
            Finding detail, review and source evidence
          </Link>
          <p>
            {row.finding.effective_requirement.category} ·{' '}
            {row.finding.effective_requirement.requirement_type}
          </p>
          <p>
            Review: {row.finding.review_status} · Grounding: {row.finding.grounding_status}
          </p>
          <p>Mapping: {row.finding.mapping.status}</p>
          {row.finding.mapping.mapping_id && (
            <p>
              Mapping ID: {row.finding.mapping.mapping_id} · Revision:{' '}
              {row.finding.mapping.revision} · ALL_OF
            </p>
          )}
          <p>
            Selected required skills:{' '}
            {skills(row.finding.mapping.skill_ids) || 'No explicit mapping'}
          </p>
          <p>Recorded coverage: {row.coverage}</p>
          <p>
            All required skills recorded: {row.individual_counts.all_required_recorded} · Some
            required skills recorded: {row.individual_counts.some_required_recorded} · No required
            skills recorded: {row.individual_counts.no_required_recorded}
          </p>
          {!!row.non_evaluation_reasons.length && (
            <p>Not evaluated because: {row.non_evaluation_reasons.join(', ')}</p>
          )}
          {!!row.finding.mapping.unavailable_reasons.length && (
            <p>Mapping unavailable: {row.finding.mapping.unavailable_reasons.join(', ')}</p>
          )}
          {!!row.individual_coverage.length && <IndividualCoverage row={row} skills={skills} />}
          <details>
            <summary>Original requirement and source quotations</summary>
            <p>{row.finding.original_requirement.statement}</p>
            {row.finding.source_references.map((source, i) => (
              <blockquote key={i}>
                <p>{source.quote}</p>
                <Link
                  to={`/tenders/documents/${encodeURIComponent(source.document_id)}/pages/${source.page_number}`}
                >
                  View source PDF page {source.page_number}
                </Link>
              </blockquote>
            ))}
          </details>
          <p>
            Qualification: {row.qualification} · Capacity: {row.capacity_evaluation} · Reviewed
            meaning: {row.reviewed_meaning_verification}
          </p>
          <ul aria-label="Evidence limitations">
            {row.limitations.map((limitation, i) => (
              <li key={i}>
                {limitation.code}: {limitation.explanation}
              </li>
            ))}
          </ul>
        </article>
      ))}
    </section>
  );
}
