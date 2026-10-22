# Local capability snapshot HTTP API (UI-1b.4b.1)

This API exposes the existing schema 1.0 validator and atomic SQLite importer. It does not
fetch Team Capacity Engine data, invoke a CLI, create mappings or run matching/assessments.
No frontend is included. The independent capability contract and evidence limitations remain
unchanged; see [snapshot import](team-capacity.md).

## Enable only for local synthetic development

Both endpoints are disabled by default. From the TenderCite repository, using an existing or
fresh **disposable/synthetic** data directory, set the flag and restart FastAPI:

```powershell
$env:TENDERCITE_CAPABILITY_SNAPSHOT_API_ENABLED = "true"
$env:TENDERCITE_DATA_DIR = "data/capabilities/http-synthetic"
$env:TENDERCITE_RETRIEVAL_ENABLED = "false"
.\.venv\Scripts\python.exe -m uvicorn tendercite.main:app --host 127.0.0.1 --port 8001
```

```sh
TENDERCITE_CAPABILITY_SNAPSHOT_API_ENABLED=true TENDERCITE_DATA_DIR=data/capabilities/http-synthetic TENDERCITE_RETRIEVAL_ENABLED=false .venv/bin/python -m uvicorn tendercite.main:app --host 127.0.0.1 --port 8001
```

Do not use real employee records, a business database or the private BfS pilot. A flag and an
acknowledgement are operator declarations, not automatic proof of synthetic origin. This is
unauthenticated local development software, without public-deployment or tenancy guarantees.
The setting gates validation as well as import, before body reading or repository access.

## Request contract

| Operation | Request | Additional import acknowledgement |
| --- | --- | --- |
| `POST /api/v1/capability-snapshots/validate` | Raw schema 1.0 snapshot JSON | None |
| `POST /api/v1/capability-snapshots/import` | The same raw snapshot JSON | `X-TenderCite-Acknowledge-Local-Synthetic-Data: true` |

Use `Content-Type: application/json` (an optional charset parameter is accepted). Content encoding
must be absent or `identity`; gzip and multipart uploads are rejected. The body is the complete
snapshot itself, **without an envelope**. No path, URL, database selector or CLI argument is
accepted. The acknowledgement header must occur once with the exact value `true`; missing,
false, numeric, padded or ambiguous values are rejected before reading/importing the body.

The exact raw-body bound is **16,777,216 bytes (16 MiB)**, shared with the CLI validator. It is
enforced during reading even without Content-Length, with a misleading length, or over multiple
ASGI body chunks. Oversized bodies stop reading; malformed/duplicate length headers and completed
bodies that disagree with their declared length fail safely. No decompression is performed.

Raw bytes pass through `validate_capability_json` before FastAPI's JSON parsing, preserving duplicate
key detection at every level. The typed request uses the same validator in strict JSON mode;
import also retains repository revalidation. Schema version, canonical hash, UUID ordering,
references, capacity types/nullability, source limitations and privacy allowlist are unchanged.
No invalid records are dropped, repaired or partially imported.

The original synthetic producer fixture supplies a complete example; no real workforce payload
is embedded in this guide. In a second terminal, PowerShell 7 can submit those bytes explicitly:

```powershell
$body = [System.IO.File]::ReadAllBytes((Resolve-Path "tests/fixtures/capabilities/tendercite-capability-v1.json"))
Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8001/api/v1/capability-snapshots/validate" -ContentType "application/json" -Body $body
Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8001/api/v1/capability-snapshots/import" -ContentType "application/json" -Headers @{"X-TenderCite-Acknowledge-Local-Synthetic-Data"="true"} -Body $body
```

```sh
curl --fail-with-body -H 'Content-Type: application/json' --data-binary @tests/fixtures/capabilities/tendercite-capability-v1.json http://127.0.0.1:8001/api/v1/capability-snapshots/validate
curl --fail-with-body -H 'Content-Type: application/json' -H 'X-TenderCite-Acknowledge-Local-Synthetic-Data: true' --data-binary @tests/fixtures/capabilities/tendercite-capability-v1.json http://127.0.0.1:8001/api/v1/capability-snapshots/import
```

These commands read a caller-local fixture; they do not ask the server to open a path. Validation
has no repository dependency and does not create a database or data directory. Import uses the
normal configured repository and its existing initialization/import behavior; no schema migration
is added. HTTP handlers never invoke CLI commands or make external requests.

## Metadata-only responses

Both operations return HTTP 200 on success:

```json
{
  "status": "VALIDATED",
  "content_hash": "sha256:e15ea7ea84176a4a9edf4bce710ede9008b620aa8329ddc14cb799354e9d6134",
  "schema_version": "1.0",
  "source_system": "team-capacity-engine",
  "assessment_period": {"start_date": "2027-02-01", "end_date": "2027-02-07"},
  "member_count": 3,
  "assignment_count": 3,
  "catalog_count": 2,
  "imported_at": null
}
```

Import returns `INSERTED` or `ALREADY_PRESENT` with the original UTC `imported_at` timestamp.
The other summary fields match validation and the CLI's shared summary helper. No member UUIDs,
engagement records, skill labels, capacity records or full payload are returned. Responses and
errors carry `Cache-Control: no-store`. OpenAPI describes the raw `CapabilitySnapshot` body,
acknowledgement header and `SnapshotResult`; no alternate snapshot serialization is introduced.

The existing `BEGIN IMMEDIATE` import, database-enforced hash identity, canonical-content and
stored-metadata checks remain authoritative. Identical concurrent imports share one record and
original timestamp. Historical snapshots remain distinct; no import becomes authoritative simply
because it is newer. Existing documents, findings, reviews, mappings and assessments are untouched.

## Fixed errors and future React integration

Errors have only `{ "detail": { "code": "..." } }`; they never echo JSON values/field names,
raw workforce records, paths, SQLite text or traces.

| HTTP | Codes |
| --- | --- |
| 400 | `INVALID_CONTENT_LENGTH`, `INCOMPLETE_BODY` |
| 404 | `CAPABILITY_SNAPSHOT_API_DISABLED` |
| 409 | `STORAGE_INTEGRITY` |
| 413 | `INPUT_TOO_LARGE` |
| 415 | `UNSUPPORTED_MEDIA_TYPE`, `UNSUPPORTED_CONTENT_ENCODING` |
| 422 | `ACKNOWLEDGEMENT_REQUIRED`, `INVALID_JSON`, `UNSUPPORTED_SCHEMA`, `INVALID_CONTRACT`, `HASH_MISMATCH` |
| 503 | `DATABASE_FAILED` |
| 500 | `CAPABILITY_SNAPSHOT_FAILED` |

An integrity conflict preserves the original record; investigate rather than overwriting/resetting
it. A failed/lost import response does not prove nothing was persisted. The future React UI should
read the downloaded file locally, enforce the same byte bound, explicitly validate, show metadata,
then require operator acknowledgement before sending the same bytes with the import header.
Do not retry imports automatically. An explicit repeat of identical input safely returns
`ALREADY_PRESENT` and the preserved timestamp. List/lookup/export remain CLI-only in this milestone.

Imported internal assignments are not professional experience, certifications or bidder eligibility.
Proficiency is not years of experience; shared capacity is not reserved or contractual availability.
No review, qualification, mapping approval, matching or Go/No-Go decision follows from an import.
The React import milestone remains separate and should start only after this API is reviewed.
