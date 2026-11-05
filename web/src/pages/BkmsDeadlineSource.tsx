import type { DeadlineEvidence } from '../api/bkmsDeadlines';

export function BkmsDeadlineSource({
  evidence,
}: {
  evidence: DeadlineEvidence | null | undefined;
}) {
  return (
    <aside className="source-page-text" aria-label="Official eForms submission deadlines">
      <strong>Official eForms submission deadlines</strong>
      {evidence ? (
        <>
          <p>
            Extraction: {evidence.notice.status}. Schema: {evidence.notice.customization_id}.
          </p>
          {evidence.notice.status === 'MULTIPLE_LOTS' && (
            <p>Lot-specific source values; no single deadline is selected.</p>
          )}
          {evidence.notice.status === 'CONFLICT' && (
            <p>
              Conflicting source data. No combined timestamp is asserted; inspect the original
              publication.
            </p>
          )}
          {evidence.notice.status === 'UNRECOGNIZED' && (
            <p>
              Unrecognized or unsupported source structure; no authoritative deadline is asserted.
            </p>
          )}
          {evidence.notice.status === 'MISSING' && (
            <p>
              No submission deadline found at the supported source paths. This does not establish
              that no deadline exists.
            </p>
          )}
          <ul>
            {evidence.notice.lots.map((lot, i) => (
              <li key={i}>
                {lot.lot_id} · {lot.status}
                {lot.periods.map((p, j) => (
                  <p key={j}>
                    Source date: {p.dates.join(' / ') || 'Not supplied'}; source time:{' '}
                    {p.times.join(' / ') || 'Not supplied'}. Offsets are preserved where supplied.
                  </p>
                ))}
              </li>
            ))}
          </ul>
          {!!evidence.notice.warnings.length && (
            <p>Warnings: {evidence.notice.warnings.join(', ')}</p>
          )}
          <details>
            <summary>Deadline extraction provenance</summary>
            <p>
              Export day: {evidence.export_day}; retrieved: {evidence.retrieved_at}. Extraction:{' '}
              {evidence.extraction_version}.
            </p>
            <p>
              Enrichment: {evidence.enrichment_id}. eForms archive SHA-256:{' '}
              {evidence.archive_sha256}. XML record SHA-256: {evidence.notice.record_sha256}.
            </p>
          </details>
        </>
      ) : (
        <p>
          Not enriched for this exact daily snapshot. Explicitly load its official eForms export
          above; missing values are not inferred.
        </p>
      )}
      <p>
        These source values are separate from the original OCDS snapshot and the local Opportunity
        deadline. Saving a notice does not adopt these values. A future deadline does not prove
        bidding is open or establish eligibility.
      </p>
    </aside>
  );
}
