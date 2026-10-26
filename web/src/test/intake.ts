import type {
  IntakeSummary,
  RequirementDraft,
  SourceDocument,
  CuratedTenderIntake,
} from '../api/tenderciteIntake';
export const sources: SourceDocument[] = [
  { id: 'doc-one', filename: 'Synthetic A.pdf', sha256: 'a'.repeat(64), page_count: 3 },
  { id: 'doc-two', filename: 'Synthetic B.pdf', sha256: 'b'.repeat(64), page_count: 2 },
  { id: 'unused-doc', filename: 'Unused.pdf', sha256: 'c'.repeat(64), page_count: 1 },
];
export const quote = '  Synthetic PostgreSQL requirement.\n  Supporting text.  ';
export function draft(item_id = 'item-1', document_id = 'doc-one'): RequirementDraft {
  return {
    item_id,
    statement: 'Synthetic candidate requirement',
    category: 'MUST',
    requirement_type: 'TECHNICAL',
    interpretation_notes: 'Manual synthetic note',
    evidence: [{ document_id, page_number: '2', quote }],
  };
}
export function summary(
  intake: CuratedTenderIntake,
  status: IntakeSummary['status'] = 'VALIDATED',
): IntakeSummary {
  return {
    schema_version: 'curated-tender-intake-result-1',
    status,
    analysis_id: `curated-${'d'.repeat(64)}`,
    intake_content_hash: `sha256:${'d'.repeat(64)}`,
    source_origin: 'manual_operator_entry',
    intake_schema_version: 'curated-tender-intake-1',
    source_count: intake.documents.length,
    finding_count: intake.findings.length,
    quotation_count: intake.findings.reduce((n, f) => n + f.evidence.length, 0),
  };
}
