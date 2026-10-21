# Local manual intake HTTP API (UI-1b.2a)

These endpoints expose the existing curated-intake repository operations. They do not run the
CLI, extract requirements, load models, fetch URLs, or perform reviews. Use the existing local
FastAPI server on `127.0.0.1:8001` for the unified development workspace. There is no
authentication: this is not a public deployment interface. The confirmation flag is an operator
acknowledgement, not authentication, proof of authorship or independent human verification.

## Contract for the React frontend

Both endpoints require `Content-Type: application/json` and UTF-8 JSON. The **entire raw body**,
including the commit envelope, is limited to **1,048,576 bytes**. The existing contract's canonical
content limit also applies. The server counts streamed bytes independently of Content-Length;
compressed bodies, duplicate JSON keys, non-finite numbers and unsupported fields are rejected.
Request and response schemas, including error bodies, are published in `/openapi.json` and `/docs`.
The existing development proxy can forward these paths without any proxy change.

### `POST /api/v1/curated-intakes/validate`

Body: the existing `CuratedTenderIntake` JSON object directly, **not** an `intake` wrapper.
See the [versioned intake contract](curated-tender-intake.md#strict-versioned-manual-contract).
It contains `schema_version: "curated-tender-intake-1"`,
`source_origin: "manual_operator_entry"`, `purpose`, optional `operator_alias`, `documents` and
`findings`. Documents use existing production document IDs and expected PDF SHA-256 values
(64 lowercase hex characters, no `sha256:` prefix). Findings contain `item_id`, `statement`,
`category`, `requirement_type`, and one or more `evidence` objects with `document_id`,
1-based physical PDF `page_number`, and exact `quote`. Optional source/interpretation notes retain
the existing bounds. No path, URL-fetch, database-selector or CLI-argument field is accepted.

Returns **HTTP 200**, existing `CuratedIntakeResult` with status `VALIDATED`. It reads sources
in one consistent repository transaction and does not persist an analysis or findings. Validation
is not a reservation or permission token; the frontend must retain the full intake for commit.

### `POST /api/v1/curated-intakes/commit`

Body:

```typescript
type CuratedIntakeCommitRequest = {
  intake: CuratedTenderIntake; // the JSON contract from OpenAPI, unchanged from validate
  confirm_manual_intake: true;
};
```

`intake` is the same complete object accepted by validate. Confirmation is required and must be the JSON boolean `true`;
missing, false, numeric and string substitutes fail. The envelope forbids extra fields.

Returns **HTTP 200** with status `CREATED` or `ALREADY_PRESENT`. Commit rechecks sources inside
the existing `BEGIN IMMEDIATE` transaction and atomically inserts the ordinary analysis/findings.
It does not accept a validation-summary ID as a substitute for the intake. Validation and commit
are separate transactions: a successful dry-run does not guarantee a later commit will succeed.

Both successful responses have these fields:

```typescript
type CuratedIntakeSummary = {
  schema_version: 'curated-tender-intake-result-1';
  status: 'VALIDATED' | 'CREATED' | 'ALREADY_PRESENT';
  analysis_id: string;             // curated- followed by 64 hex characters
  intake_content_hash: string;     // sha256: followed by 64 hex characters
  source_origin: 'manual_operator_entry';
  intake_schema_version: 'curated-tender-intake-1';
  source_count: number;
  finding_count: number;
  quotation_count: number;
};
```

The server reuses deterministic identities from canonical intake content. Identical commits
reuse the analysis; distinct intake content produces a distinct revision/identity without
replacing earlier findings. Concurrent identical commits serialize through the existing SQLite
operation. Changed stored source text, identity collisions or storage corruption are not repaired
or overwritten silently. The summary contains no requirement text or quotations.

New findings are always `UNREVIEWED`, with no invented confidence. There are no review events,
mappings, approvals or Go/No-Go decisions created by intake. Later explicit reviews survive
idempotent commits. Use `GET /api/v1/analyses/{analysis_id}` for original provenance and
`GET /api/v1/findings?analysis_run_id=...` for current reviewed findings.

### Stable application errors

These endpoints return `{"detail":{"code":"ERROR_CODE"}}`; they do not return Pydantic input
echoes, document text, DB details or exception traces. Do not assume other existing API routes
use this envelope. In particular, do not implement automatic commit retries on transport failures:
if the response is lost, persistence can be uncertain. The deterministic analysis ID from a prior
validation can help inspect the existing analysis before an explicit idempotent retry.

| HTTP | Codes |
| --- | --- |
| 400 | `INVALID_CONTENT_LENGTH` |
| 404 | `SOURCE_DOCUMENT_NOT_FOUND`, `SOURCE_PAGE_NOT_FOUND` |
| 409 | `SOURCE_DOCUMENT_NOT_PDF`, `SOURCE_PDF_HASH_MISMATCH`, `SOURCE_PAGES_INCOMPLETE`, `INTAKE_IDENTITY_CONFLICT`, `INTAKE_SOURCE_STATE_CHANGED` |
| 413 | `INTAKE_TOO_LARGE` |
| 415 | `UNSUPPORTED_MEDIA_TYPE`, `UNSUPPORTED_CONTENT_ENCODING` |
| 422 | `INVALID_CURATED_INTAKE`, `MANUAL_INTAKE_CONFIRMATION_REQUIRED`, `SOURCE_QUOTE_NOT_VERIFIED` |
| 500 | `INTAKE_STORAGE_INTEGRITY`, `DATABASE_FAILED`, `CURATED_INTAKE_FAILED` |

## Synthetic-only PowerShell example

Use a new disposable backend data directory, **not** the private BfS pilot or any existing
operator database. In a server terminal from the TenderCite repository root:

```powershell
if (Test-Path data/curated/http-demo-server) { throw 'Choose a fresh disposable directory' }
$env:TENDERCITE_DATA_DIR = 'data/curated/http-demo-server'
$env:TENDERCITE_RETRIEVAL_ENABLED = 'false'
.\.venv\Scripts\python.exe -m uvicorn tendercite.main:app --host 127.0.0.1 --port 8001
```

In another terminal, generate an original PDF using the existing synthetic PDF generator and
upload it using the normal document endpoint. No third-party source documents are needed:

```powershell
.\.venv\Scripts\python.exe -c "from pathlib import Path; from evaluation.generate import make_pdf; p=Path('data/curated/http-demo-input'); p.mkdir(parents=True, exist_ok=False); (p/'synthetic.pdf').write_bytes(make_pdf(['The contractor must provide PostgreSQL development experience.']))"
$document = curl.exe --fail --silent --show-error -F 'file=@data/curated/http-demo-input/synthetic.pdf;type=application/pdf' http://127.0.0.1:8001/api/v1/documents | ConvertFrom-Json
$quote = 'The contractor must provide PostgreSQL development experience.'
$intake = @{schema_version='curated-tender-intake-1'; source_origin='manual_operator_entry'; purpose='Synthetic HTTP example'; operator_alias='synthetic_operator'; documents=@(@{document_id=$document.id; expected_pdf_sha256=$document.sha256}); findings=@(@{item_id='postgresql'; statement=$quote; category='MUST'; requirement_type='TECHNICAL'; evidence=@(@{document_id=$document.id; page_number=1; quote=$quote})})}
$json = $intake | ConvertTo-Json -Depth 10
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8001/api/v1/curated-intakes/validate -ContentType 'application/json' -Body ([Text.Encoding]::UTF8.GetBytes($json))
# Inspect the candidate interpretation before explicitly acknowledging commit.
$commit = @{intake=$intake; confirm_manual_intake=$true} | ConvertTo-Json -Depth 10
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8001/api/v1/curated-intakes/commit -ContentType 'application/json' -Body ([Text.Encoding]::UTF8.GetBytes($commit))
```

Repeating the final request returns `ALREADY_PRESENT`; it does not fabricate a review. Quote
verification establishes occurrence in the stored parser output, **not semantic/legal correctness,
exhaustive discovery, compliance, qualification or bidder eligibility**. Evaluation annotations and
staged LLM findings remain separate formats; no migration bridge is provided. Keep generated
artifacts under ignored `data/curated/` with private local permissions. Nothing in UI-1b.2a changes
the React frontend or adds intake, review or capability-matching UI.
