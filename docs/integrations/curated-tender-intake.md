# Curated tender requirement intake (4.3a)

This offline workflow persists explicitly entered, source-backed **candidate requirements** as
an ordinary TenderCite `AnalysisRun`. It starts with PDFs already registered through the normal
document workflow. It does not extract requirements, invoke providers, index vectors, review
findings, approve skill associations or determine bidder qualification.

## Existing documents and boundaries

Obtain PDFs only through permitted downloads and check confidentiality, provenance and reuse
rights yourself. A PDF hash, public URL or quotation match does not resolve those questions.
Never send procurement documents, page exports or intake files to third-party systems.

Use the existing document workflow with retrieval disabled. Start the existing API locally in
one terminal; it requires neither an LLM nor embedding downloads for document ingestion:

```powershell
$env:TENDERCITE_RETRIEVAL_ENABLED = 'false'
$env:TENDERCITE_DATA_DIR = 'data'
.venv\Scripts\python.exe -m uvicorn tendercite.main:app --host 127.0.0.1 --port 8000
```

Linux:

```sh
TENDERCITE_RETRIEVAL_ENABLED=false TENDERCITE_DATA_DIR=data .venv/bin/python -m uvicorn tendercite.main:app --host 127.0.0.1 --port 8000
```

The intake CLI opens SQLite directly and requires no server. The optional
[local HTTP intake endpoints](curated-intake-http.md) expose the same validation and atomic commit
for clients; no second ingestion pipeline or public-deployment security model is introduced.
Upload through the existing UI or API. In another PowerShell terminal, with a permitted PDF
(each command is one line):

```powershell
$document = curl.exe --fail --silent --show-error -F 'file=@C:/permitted/tender.pdf;type=application/pdf' http://127.0.0.1:8000/api/v1/documents | ConvertFrom-Json
$documentId = $document.id; $pdfSha256 = $document.sha256
New-Item -ItemType Directory -Force data/curated | Out-Null
if (Test-Path data/curated/page-1.json) { throw 'Choose a fresh private page output' }; Invoke-WebRequest -Uri "http://127.0.0.1:8000/api/v1/documents/$documentId/pages/1" -OutFile data/curated/page-1.json
code data/curated/page-1.json
```

Keep the directory ACL private. `GET /api/v1/documents` lists existing metadata: select the intended
record rather than guessing an ID. Its `sha256` is the original PDF byte SHA-256: 64 lowercase hex
characters **without** `sha256:`. Pages are 1-based physical PDF pages, not printed page labels.
Inspect the extracted page and original PDF together; do not invent quotes when extraction is
unusable. The normal upload route already supplies parsing, chunking and hash deduplication.

Evaluation corpus IDs such as `sha256:…` are **not automatically production document IDs**.
Human gold, external QA annotations and staged LLM findings keep their separate schemas, origins
and review meanings. This CLI accepts none of those envelopes and adds no migration bridge.
A VERIFIED evaluation annotation is not a CONFIRMED production review, and renaming an origin
cannot authenticate authorship.

## Strict versioned manual contract

Save UTF-8 JSON without a BOM under `data/curated/`, with actual registered source identities:

```json
{
  "schema_version": "curated-tender-intake-1",
  "source_origin": "manual_operator_entry",
  "operator_alias": "operator_01",
  "purpose": "Manually curated candidates for operator review",
  "documents": [{
    "document_id": "EXISTING_DOCUMENT_ID",
    "expected_pdf_sha256": "0000000000000000000000000000000000000000000000000000000000000000",
    "source_note": "Non-sensitive local source/version note"
  }],
  "findings": [{
    "item_id": "postgresql",
    "statement": "The contractor must provide PostgreSQL development experience.",
    "category": "MUST",
    "requirement_type": "TECHNICAL",
    "evidence": [{
      "document_id": "EXISTING_DOCUMENT_ID",
      "page_number": 1,
      "quote": "The contractor must provide PostgreSQL development experience."
    }],
    "interpretation_notes": "Candidate interpretation; requires explicit review"
  }]
}
```

This is self-authored synthetic text, not a quote from a genuine tender. Replace all placeholders
and candidates with your explicitly selected sources. Multiple quotations and PDFs are supported.
The declared document set must equal the referenced set; unused/undeclared sources and duplicate
document/item IDs fail. The independent strict, frozen Pydantic contract forbids unexpected fields
and unsupported versions. Limits: 1 MiB input/canonical content, 1–100 documents/findings,
1–20 citations per finding, 128-character ASCII IDs, 4,000-character nonblank statements/quotes,
2,000-character purpose and 1,000-character optional notes. Non-personal aliases allow 1–64 ASCII
letters/digits/underscores/hyphens. They are declarations, not authentication. Categories/types
are the existing enums; review status, model confidence and qualification labels are not inputs.

## Validate, explicitly commit, inspect

`--database` precedes the command and names an existing local `.db`. Without it the CLI uses
`TENDERCITE_DATA_DIR/tendercite.db`, default `data/tendercite.db`, through local-only settings.
Validation/inspection never initialize a database. Commit also skips initialization and will not
recreate a database whose path disappears. No provider configuration is required.

```powershell
.venv\Scripts\python.exe -m tendercite.cli.curated_intake --database data/tendercite.db validate --input data/curated/intake.json
.venv\Scripts\python.exe -m tendercite.cli.curated_intake --database data/tendercite.db commit --input data/curated/intake.json --confirm-manual-intake
.venv\Scripts\python.exe -m tendercite.cli.curated_intake --database data/tendercite.db inspect --analysis-id ANALYSIS_ID
.venv\Scripts\python.exe -m tendercite.cli.curated_intake --database data/tendercite.db inspect --analysis-id ANALYSIS_ID --output data/curated/original-intake-analysis.json
```

Linux uses the same arguments with `.venv/bin/python`. Use `umask 077` and private 0700 directories;
Windows operators must restrict directory/SQLite ACLs. Console output contains only IDs, content
hash, origin/version, counts and VALIDATED/CREATED/ALREADY_PRESENT/INSPECTED status. Original text,
quotes, purpose and notes require an explicitly requested **new** private inspection file.

Validation checks document existence, PDF media type, expected stored hash, complete contiguous
page inventory, declared page existence and each quote against its actual stored page. It uses
unchanged `validate_evidence`: case-sensitive whitespace-normalized matching. No quote is repaired,
relocated or matched against another page/document. EvidenceRefs retain normalized quotes/offsets
and normalized-page hashes; provenance retains original entered quotes and cited-page raw stored
text SHA-256 values. Any invalid finding rejects the **complete intake**. Fixed source-free errors
include SOURCE_DOCUMENT_NOT_FOUND, SOURCE_PDF_HASH_MISMATCH, SOURCE_DOCUMENT_NOT_PDF,
SOURCE_PAGES_INCOMPLETE, SOURCE_PAGE_NOT_FOUND and SOURCE_QUOTE_NOT_VERIFIED. Dry-run uses one
consistent read transaction and writes nothing; success proves neither interpretation nor coverage.

File processing reuses bounded no-follow regular-file reads, duplicate/nonfinite detection and
exclusive atomic private export. Inside this checkout use `data/curated/`, already Git-ignored
and Docker-excluded; existing capability handoff files can also remain there. Explicit local files
outside checkouts/build contexts are supported. URLs, shares, links, traversal and unsafe checkout
paths fail. Exports use 0600 POSIX files and guarded Windows publication, never overwrite or leave
a partial final file. SQLite remains trusted local state, without authentication/signatures.

## Atomic persistence, identity and provenance

No tables or destructive migrations are added. The repository reuses the normal analysis/finding
insertion helper on its current connection. `BEGIN IMMEDIATE` surrounds source revalidation,
identity checks and insertion of the analysis and every finding. There are no independent nested
repository transactions. Failures roll back completely; competing duplicate commits serialize.

Canonical content includes validated defaults, UTF-8, sorted JSON keys, compact separators and
unescaped Unicode. Sources sort by ID, findings by item ID and citations by document/page/original
quote. JSON formatting/array order does not affect identity. Changing any actual string, alias,
note, source identity or candidate creates a distinct revision.

Content identity is `sha256:` plus the canonical digest. Analysis ID is `curated-` plus that digest;
finding ID is `curated-finding-` plus SHA-256 of compact JSON `[analysis_id,item_id]`. Explicit source
registry IDs and PDF hashes participate; a different registry with different IDs is a different
intake even for identical PDFs. Creation time is excluded. There is no caller-selected analysis ID
or overwrite mode. Retries compare complete content, original analysis/finding records and cited
page fingerprints, not just the hash. ALREADY_PRESENT never rewrites reviews, assessments or
mappings. Conflicting/corrupt records fail; changed cited-page text fails INTAKE_SOURCE_STATE_CHANGED
even when the quote still occurs. New revisions must be separately reviewed/mapped.

`AnalysisRun.curated_intake` contains `curated-tender-intake-provenance-1`: original payload,
content hash, original item-to-finding identities and page fingerprints. Honest markers are
`provider=manual_operator_entry`, `model=not_invoked`, `embedding_model=not_invoked` and
`prompt_version=manual-intake-no-prompt-1`; provider/embedding/retrieval flags are false. Retrieval
plan/chunks are empty; the existing request top_k is unused compatibility metadata. Findings have
no model output or invented confidence and **all start UNREVIEWED**, with no fabricated ReviewEvent.
Inspection exports the original snapshot; normal finding/review methods or 4.2c overview show
current reviewed values.

## Explicit review and capability workflow

Review source, interpretation, category/type and exceptions yourself. Use the existing review API;
CONFIRM/MODIFY/REJECT remain explicit. Example with a selected actual finding:

```powershell
$reviewJson = '{"action":"CONFIRM","comment":"Explicit operator review of this candidate"}'
Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8000/api/v1/findings/FINDING_ID/reviews" -ContentType application/json -Body $reviewJson | Out-Null
```

MODIFY requires a complete statement/category/requirement_type in `reviewed_value`. Confirmation
is an interpretation, not fulfillment or legal correctness. Unreviewed intake cannot prepare an
eligible mapping. After explicit review, follow the [4.2c guide](local-capability-workflow.md):
exact analysis/snapshot selection, private overview, manual skill UUID selection, draft preparation,
existing draft creation, explicit approval and private report. No skills are inferred. A later
CONFIRM/MODIFY/REJECT makes the prior approval stale, even for identical text. Qualification stays
NOT_VERIFIED, capacity NOT_EVALUATED; intake/reporting never updates existing Go/No-Go decisions.

## Complete synthetic operator verification

The script creates a fresh disposable database, generates a two-page PDF and uses the existing
upload/parser/chunker in-process **without network sockets**. It writes explicitly authored input,
validates/commits/inspects it, simulates production review, imports the exact pinned synthetic
workforce snapshot, explicitly selects PostgreSQL's UUID, creates/approves a mapping, exports a
report, then simulates changed review and exports a stale report. Simulated review/approval is
**not genuine human verification**. Nonempty directories are refused; no genuine tender/personnel
files are used. Each PowerShell command is one line, using a fresh private directory:

```powershell
.venv\Scripts\python.exe -m examples.curated_tender_intake --directory data/curated/operator-demo
.venv\Scripts\python.exe -m tendercite.cli.curated_intake --database data/curated/operator-demo/tendercite.db validate --input data/curated/operator-demo/intake.json
$analysisId = (.venv\Scripts\python.exe -m tendercite.cli.curated_intake --database data/curated/operator-demo/tendercite.db commit --input data/curated/operator-demo/intake.json --confirm-manual-intake | ConvertFrom-Json).analysis_id
.venv\Scripts\python.exe -m tendercite.cli.curated_intake --database data/curated/operator-demo/tendercite.db inspect --analysis-id $analysisId --output data/curated/operator-demo/operator-inspection.json
.venv\Scripts\python.exe -m tendercite.cli.capability_workflow --database data/curated/operator-demo/tendercite.db report --input data/curated/operator-demo/assessment-selection.json --output data/curated/operator-demo/operator-assessment.json
code data/curated/operator-demo/approved-assessment.json
code data/curated/operator-demo/operator-assessment.json
```

Linux:

```sh
umask 077
.venv/bin/python -m examples.curated_tender_intake --directory data/curated/operator-demo
.venv/bin/python -m tendercite.cli.curated_intake --database data/curated/operator-demo/tendercite.db validate --input data/curated/operator-demo/intake.json
.venv/bin/python -m tendercite.cli.curated_intake --database data/curated/operator-demo/tendercite.db commit --input data/curated/operator-demo/intake.json --confirm-manual-intake
.venv/bin/python -m tendercite.cli.capability_workflow --database data/curated/operator-demo/tendercite.db report --input data/curated/operator-demo/assessment-selection.json --output data/curated/operator-demo/operator-assessment.json
```

Expected: VALIDATED → CREATED, repeat ALREADY_PRESENT, original findings UNREVIEWED. Before review
matching is NOT_EVALUATED. The simulated approved report has two individuals with all selected
assignments recorded and one with none; after changed review it is STALE/NOT_EVALUATED. These are
synthetic assignment counts, not accuracy, legal compliance, experience/certificate verification,
reserved capacity or bidder eligibility.

## Limits

Occurrence proves neither interpretation, completeness, genuine authorship/review, rights, competence
nor eligibility. Hashes are checked against registered metadata/stored pages; intake does not reopen,
reparse or attest current on-disk PDF bytes. Aliases/hashes are not authentication. Administrators can
replace trusted application state. Operators must independently review interpretation and regenerate
reports after changes. No annotation migration, OCR, extraction, automatic mapping, independent
qualification evidence, capacity reservation, new API/UI or external transmission is added.
