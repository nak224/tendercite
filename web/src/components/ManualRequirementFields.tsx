import { Link } from 'react-router-dom';
import {
  categories,
  requirementTypes,
  type RequirementDraft,
  type SourceDocument,
} from '../api/tenderciteIntake';

export function ManualRequirementFields({
  value,
  number,
  documents,
  onChange,
  onRemove,
  removable,
}: {
  value: RequirementDraft;
  number: number;
  documents: SourceDocument[];
  onChange: (value: RequirementDraft) => void;
  onRemove: () => void;
  removable: boolean;
}) {
  const citation = value.evidence[0]!;
  const source = documents.find((doc) => doc.id === citation.document_id);
  const page = Number(citation.page_number);
  function evidence(change: Partial<typeof citation>) {
    onChange({ ...value, evidence: [{ ...citation, ...change }, ...value.evidence.slice(1)] });
  }
  return (
    <fieldset>
      <legend>Requirement {number}</legend>
      <label>
        Requirement statement
        <textarea
          required
          maxLength={4000}
          rows={2}
          value={value.statement}
          onChange={(e) => onChange({ ...value, statement: e.target.value })}
        />
      </label>
      <div className="form-grid">
        <label>
          Category
          <select
            required
            value={value.category}
            onChange={(e) =>
              onChange({ ...value, category: e.target.value as RequirementDraft['category'] })
            }
          >
            <option value="">Select category</option>
            {categories.map((category) => (
              <option key={category}>{category}</option>
            ))}
          </select>
        </label>
        <label>
          Requirement type
          <select
            required
            value={value.requirement_type}
            onChange={(e) =>
              onChange({
                ...value,
                requirement_type: e.target.value as RequirementDraft['requirement_type'],
              })
            }
          >
            <option value="">Select requirement type</option>
            {requirementTypes.map((type) => (
              <option key={type}>{type}</option>
            ))}
          </select>
        </label>
      </div>
      <label>
        Source document
        <select
          required
          value={citation.document_id}
          onChange={(e) => evidence({ document_id: e.target.value })}
        >
          <option value="">Select uploaded PDF</option>
          {documents.map((doc) => (
            <option key={doc.id} value={doc.id}>
              {doc.filename} · {doc.page_count} pages
            </option>
          ))}
        </select>
      </label>
      <label>
        Physical PDF page
        <input
          type="number"
          required
          min={1}
          max={source?.page_count}
          step={1}
          value={citation.page_number}
          onChange={(e) => evidence({ page_number: e.target.value })}
        />
      </label>
      {source && Number.isSafeInteger(page) && page >= 1 && page <= source.page_count && (
        <p>
          <Link
            to={`/tenders/documents/${encodeURIComponent(source.id)}/pages/${page}`}
            target="_blank"
            rel="noopener noreferrer"
          >
            Inspect source page (opens new tab)
          </Link>
        </p>
      )}
      <label>
        Exact supporting quotation
        <textarea
          required
          maxLength={4000}
          rows={3}
          value={citation.quote}
          onChange={(e) => evidence({ quote: e.target.value })}
        />
      </label>
      <label>
        Interpretation note (optional)
        <textarea
          maxLength={1000}
          rows={2}
          value={value.interpretation_notes}
          onChange={(e) => onChange({ ...value, interpretation_notes: e.target.value })}
        />
      </label>
      <button type="button" disabled={!removable} onClick={onRemove}>
        Remove requirement {number}
      </button>
    </fieldset>
  );
}
