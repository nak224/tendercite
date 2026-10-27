# Local capability assessment report API (UI-1b.6a)

`POST /api/v1/capability-workflow/report` exposes the existing `assessment_report()` service
and `AssessmentReport` contract. It is a point-in-time assessment-support report, not a saved
decision or an overall Go/No-Go verdict. No React report view is added in this milestone.

## Separate local opt-in

`TENDERCITE_CAPABILITY_REPORT_API_ENABLED=false` by default. Explicitly enable it and restart
FastAPI only for a local development database whose contents you are permitted to inspect.
The mapping and snapshot API flags neither enable nor are required by this report endpoint.
The report uses only the configured `TENDERCITE_DATA_DIR/tendercite.db`, opened **read-only**;
it never initializes a missing database or changes its schema. No request can select a path,
URL, filesystem artifact, provider or alternative database.

Reports contain source quotations, reviewed requirement text, catalog labels and **pseudonymous
individual person UUIDs with matched/missing skill UUIDs**. These are private workforce records,
even without names or emails. Keep the server bound to loopback and responses in private local
storage. The opt-in flag is not authentication or verification of synthetic data. This API has
no public-deployment, access-control or multi-tenancy guarantees. Do not use private BfS or real
personnel data for tests. Responses and handled errors carry `Cache-Control: no-store`.

## Request and response

The JSON body is the existing strict `AssessmentSelection` contract, without an envelope:

```json
{
  "schema_version": "capability-workflow-assessment-selection-1",
  "analysis_id": "synthetic-analysis",
  "snapshot_hash": "sha256:e15ea7ea84176a4a9edf4bce710ede9008b620aa8329ddc14cb799354e9d6134",
  "finding_ids": ["synthetic-finding"]
}
```

These identifiers refer to self-authored example data; use identities from your selected
existing analysis and imported snapshot. All selected findings must belong to that analysis.
The full snapshot hash is mandatory; there is no latest-snapshot fallback. `finding_ids` is
required, unique, at most **100** entries, and canonically sorted by the existing contract.
An explicit empty array returns an empty report with zero summary counts, while still checking
analysis/snapshot identity. It never means "all findings". Unknown fields are rejected. The
existing schema-version default remains supported; examples send the version explicitly.

On success, HTTP **200** returns the unchanged `AssessmentReport`:

- `schema_version=capability-workflow-report-1`, the exact analysis and snapshot identities,
  snapshot period/catalog/limitations and summary counts.
- `matching_contract_version=capability-matching-result-1`,
  `algorithm_version=explicit-uuid-membership-1`, `purpose=ASSESSMENT_SUPPORT_ONLY`.
- Selected finding rows with original/effective requirements, review/grounding state, source
  references, current review binding and mapping state/revision/skills/unavailability reasons.
- Existing recorded coverage, per-individual coverage, aggregate counts, non-evaluation reasons
  and evidence limitations. Ordering remains deterministic for identical loaded state.
- `qualification=NOT_VERIFIED`, `capacity_evaluation=NOT_EVALUATED`; reviewed meaning remains
  `NOT_ASSESSED`. No ranking, score or inferred duration/qualification is introduced.

Only current eligible, review-bound, catalog-valid **APPROVED** mappings participate in the
unchanged UUID-membership matcher. NOT_FOUND, DRAFT, STALE and REVOKED remain visible and yield
NOT_EVALUATED rows as applicable; unreviewed/rejected or ungrounded findings remain ineligible.
An old approval is never substituted for the current mapping revision. A new review can make
an approval stale even when the text is identical. Reports read source state in one consistent
SQLite transaction; regenerate them after reviews, mappings or snapshots change.

## Failures and transport limits

Errors use only `{"detail":{"code":"FIXED_CODE"}}`, without input values, field names, SQL,
database paths, personnel records, source text or exception traces:

| HTTP | Code | Meaning |
| --- | --- | --- |
| 404 | CAPABILITY_REPORT_API_DISABLED | Separate report opt-in is off |
| 422 | INVALID_REPORT_INPUT | Invalid selection/JSON, duplicate keys/IDs, unknown fields/query selectors |
| 404 | INVALID_MAPPING_SCOPE / SNAPSHOT_NOT_FOUND | Analysis/finding scope or exact snapshot unavailable |
| 409 | MAPPING_STORAGE_INTEGRITY / STORAGE_INTEGRITY | Existing repository detected inconsistent stored state |
| 503 | DATABASE_FAILED | Existing configured database cannot be read |
| 413 | REPORT_REQUEST_TOO_LARGE | Body exceeds 65,536 bytes or query exceeds 2,048 bytes |
| 415 | UNSUPPORTED_MEDIA_TYPE / UNSUPPORTED_CONTENT_ENCODING | JSON required; only identity encoding supported |
| 400 | INVALID_CONTENT_LENGTH / INCOMPLETE_BODY | Invalid length headers or interrupted body |
| 500 | CAPABILITY_REPORT_FAILED | Unexpected failure, safely redacted |

The shared bounded capability-route parser checks the flag and transport before repository
access. It enforces the body bound while streaming, including absent/misleading Content-Length,
rejects duplicate JSON object keys, nonfinite constants and ambiguous transport headers. Query
selectors are unsupported. No writes, approvals, assessment mutations, CLI execution, provider
calls, model loading or outbound network requests occur.

## Reproducible synthetic local check

The existing generated-PDF example creates a **fresh**, ignored disposable database. It simulates
review and approval, then changes a review, so the final API report should show STALE and
NOT_EVALUATED. Simulated review/approval is not genuine human verification. Never replace a
private pilot database. Run from the repository root with installed development dependencies.
Each command below is one PowerShell line:

```powershell
.\.venv\Scripts\python.exe -m examples.curated_tender_intake --directory data/curated/report-api-demo
$env:TENDERCITE_DATA_DIR = "data/curated/report-api-demo"
$env:TENDERCITE_RETRIEVAL_ENABLED = "false"
$env:TENDERCITE_CAPABILITY_REPORT_API_ENABLED = "true"
.\.venv\Scripts\python.exe -m uvicorn tendercite.main:app --host 127.0.0.1 --port 8001
```

In a second terminal at the repository root, read the example's exact selection and show only
safe summary metadata (do not print the full report):

```powershell
$selection = Get-Content -Raw data/curated/report-api-demo/assessment-selection.json
$report = Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8001/api/v1/capability-workflow/report" -ContentType "application/json" -Body ([System.Text.Encoding]::UTF8.GetBytes($selection))
$report | Select-Object schema_version, analysis_id, snapshot_hash, summary, qualification, capacity_evaluation
```

Linux equivalents:

```sh
.venv/bin/python -m examples.curated_tender_intake --directory data/curated/report-api-demo
TENDERCITE_DATA_DIR=data/curated/report-api-demo TENDERCITE_RETRIEVAL_ENABLED=false TENDERCITE_CAPABILITY_REPORT_API_ENABLED=true .venv/bin/python -m uvicorn tendercite.main:app --host 127.0.0.1 --port 8001
```

From a second terminal, display just the summary:

```sh
curl --fail-with-body --silent --show-error -H 'Content-Type: application/json' --data-binary @data/curated/report-api-demo/assessment-selection.json http://127.0.0.1:8001/api/v1/capability-workflow/report | .venv/bin/python -c 'import json,sys; print(json.dumps(json.load(sys.stdin)["summary"], sort_keys=True))'
```

For a current approved result use an independently prepared synthetic current approval, following
the [existing mapping lifecycle](persistent-capability-mappings.md). Never silently reapprove the
example's stale mapping. Full reports must stay in ignored, Docker-excluded private local data.

## Interpretation boundaries and remaining work

Verified quote occurrence, human interpretation review, operator mapping approval and internal
workforce assignments are separate evidence layers. None establishes independent qualifications,
certification, years of experience, bidder eligibility, reserved capacity or legal compliance.
Missing assignments mean missing records, not absent skills. No team selection or automatic
Go/No-Go decision occurs, and existing assessments remain untouched.

This endpoint delegates matching and report serialization to existing services. It is synchronous
and returns the existing bounded-selection report, which may be large for a large snapshot;
there is no pagination, report persistence or access-control layer. The standalone React report
view is a separate **UI-1b.6b** milestone. Mapping revision/revocation/history still require the CLI.
