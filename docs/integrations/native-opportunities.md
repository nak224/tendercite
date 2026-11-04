# Native manual opportunities (UI-1b.8)

**Opportunities** at `/opportunities` stores local references to externally sourced public tenders,
freelance projects and other potential business opportunities. TenderCite does not host or publish
job listings. Records may be manual declarations, explicitly imported [official TED metadata](ted-discovery.md)
or [German Bekanntmachungsservice Open Data](german-procurement-discovery.md).
Their origins remain distinct; neither is independently verified qualification evidence. Manual
creation needs no document, native profile, workforce snapshot, external integration or AI model.

## Setup and manual workflow

Use the [standalone workspace setup](../web-workspace.md) and the existing fixed development proxy.
Opportunity CRUD has its own disabled-by-default flag. For an isolated synthetic workspace:

```powershell
$env:TENDERCITE_DATA_DIR = "data/curated/native-opportunity-demo"
$env:TENDERCITE_RETRIEVAL_ENABLED = "false"
$env:TENDERCITE_OPPORTUNITIES_API_ENABLED = "true"
.\.venv\Scripts\python.exe -m uvicorn tendercite.main:app --host 127.0.0.1 --port 8001
```

Linux:

```sh
TENDERCITE_DATA_DIR=data/curated/native-opportunity-demo TENDERCITE_RETRIEVAL_ENABLED=false TENDERCITE_OPPORTUNITIES_API_ENABLED=true .venv/bin/python -m uvicorn tendercite.main:app --host 127.0.0.1 --port 8001
```

Run Vite in another terminal with `TENDERCITE_PROXY_TARGET=http://127.0.0.1:8001`, then open
`http://127.0.0.1:5174/opportunities`. No profile, snapshot, mapping or report API flags are required.
Clear provider configuration for synthetic testing. Keep servers on loopback. The flag is not
authentication, accounts, multi-tenancy or public deployment readiness.

1. Choose **Create Opportunity**. Enter a title and choose FREELANCE_PROJECT, PUBLIC_TENDER or OTHER.
2. Optionally add organization/client, description, location and remote/hybrid/on-site preference.
3. Optionally enter a deadline, source platform/organization and original HTTP/HTTPS URL.
4. Add required or desired skill labels, edit them or remove rows. No catalog UUIDs are required.
   An optional requirements description preserves the operator's wording without legal interpretation.
5. Choose **Save Opportunity** and wait for server confirmation. Reopen the saved record to inspect
   or edit it; saving an edit requires its current revision. The list shows title, type,
   organization and deadline, with bounded Previous/Next pages.

There is no autosave. Unsaved edits stay in page memory. Opening another opportunity, creating a
new one or adopting a saved version asks before discarding edits. Browser reload/close uses its
native unsaved-change warning where supported. The page also visibly warns to save before leaving;
ordinary sidebar/back navigation is not a global router-blocking feature. Unsaved drafts are not
persisted. Canceling a discard confirmation retains the current form and sends no save request.

Deadlines are **date-only ISO dates**, displayed using the browser's date input and stored as
YYYY-MM-DD or null. This does not establish a submission time, time zone, deadline validity or
legal obligation. Past dates are allowed; no reminders, expiry decisions or notifications run.

## Three distinct concepts

| Record | Meaning | Current relationship |
| --- | --- | --- |
| Native opportunity | Unverified manual reference or explicitly imported official metadata; manually selected skill labels | Independent local opportunity record |
| PDF-based tender analysis | Persisted analysis/findings with source quotations and separate human review | No automatic association with opportunities |
| Native capability profile | Manually declared freelancer/company skills and preferences | Explicit normalized label comparison in Opportunity Matching |

A PUBLIC_TENDER opportunity does not create an AnalysisRun, Finding, quotation, review, assessment
or mapping. A URL is not source verification. No automatic relationship or conversion is implemented.
Existing workforce snapshot matching remains unchanged and separate. [Opportunity Matching](native-matching.md)
compares saved native skill labels explicitly, without ranking or compatibility percentages.
Saving an opportunity never performs that comparison or creates acquisition tracking automatically.

## Contracts and bounds

| Contract | Version |
| --- | --- |
| Opportunity | `native-opportunity-1` |
| Create / update | `native-opportunity-create-1` / `native-opportunity-update-1` |
| Stored record | `native-opportunity-record-1` |
| Save result / listing | `native-opportunity-save-result-1` / `native-opportunity-listing-1` |

The strict opportunity contract contains a required title (1–200 characters) and type. Optional
fields default to empty strings, empty skill list or null: organization (200), description (2000),
location (120), work arrangement (REMOTE/HYBRID/ON_SITE/null), deadline, source platform (200),
source URL (2048), required_skills (at most 100 plain labels, 1–100 each), requirements_description
(2000). Strings are trimmed; duplicate skill labels are rejected after Unicode NFKC, case-folding
and whitespace collapse. Browser checks are convenience checks; server validation is authoritative.
Source origin is `manual_operator_entry` for manual creation, or `ted_import` with immutable
`ted-opportunity-source-1` metadata for explicit TED import. German Open Data uses `bkms_import`
and `bkms-opportunity-source-1`, preserving source version, original metadata and hashes separately
from local edits. Manual records omit absent
`external_source` on serialization, preserving their previous JSON shape. Verification is always
`NOT_VERIFIED`. Unknown fields and
unsupported versions are rejected, including implicit analysis/profile relationships or verified status.

Source URLs are optional references only. HTTP/HTTPS syntax is checked without DNS/network access.
Credentials, control/whitespace characters, backslashes, malformed ports and non-HTTP schemes are
rejected. The entered URL is preserved after trimming, not rewritten or crawled. The saved link
opens only after an explicit browser click in a separate tab, with noopener/noreferrer and no
referrer. Ordinary opportunity CRUD never fetches the entered URL. The separate, default-off
discovery API contacts only the fixed official TED endpoint after an explicit operator action.

## Local HTTP API and persistence

| Method | Route under /api/v1 | Request | Result |
| --- | --- | --- | --- |
| POST | `/opportunities` | OpportunityCreate | 201 CREATED or 200 ALREADY_PRESENT |
| GET | `/opportunities?limit=50&offset=0` | limit 1..100, offset 0..1,000,000 | Bounded metadata listing in stable UUID order |
| GET | `/opportunities/{opportunity_id}` | UUID | Full versioned OpportunityRecord |
| PUT | `/opportunities/{opportunity_id}` | OpportunityUpdate | 200 UPDATED or explicit conflict |

Synthetic create example (the React UI generates identity automatically):

```json
{
  "schema_version": "native-opportunity-create-1",
  "opportunity_id": "30000000-0000-0000-0000-000000000001",
  "opportunity": {
    "schema_version": "native-opportunity-1",
    "title": "Synthetic Python project",
    "opportunity_type": "FREELANCE_PROJECT",
    "organization": "Synthetic client",
    "deadline": "2027-03-01",
    "source_platform": "Synthetic platform",
    "source_url": "https://example.invalid/project",
    "required_skills": ["Python", "FastAPI"],
    "requirements_description": "Manually supplied, not legally interpreted.",
    "source_origin": "manual_operator_entry",
    "verification_status": "NOT_VERIFIED"
  }
}
```

Updates send `{"schema_version":"native-opportunity-update-1","expected_revision":1,"opportunity":...}`
with the entire opportunity. Stable creation UUIDs permit identical repeated creation without
duplicates. Changed content or an already edited record with the same creation ID returns a
conflict; it never replaces an existing opportunity. Updates increment revisions under BEGIN
IMMEDIATE, rechecking expected_revision within the transaction and SQL predicate. Only the
current record is stored; this is not immutable revision history.

One additive `native_opportunities` table is created by normal repository initialization. Existing
profiles, documents, findings, reviews, assessments, snapshots and mappings are not migrated or
modified by opportunity saves. No foreign keys couple opportunities to those concepts.

The shared bounded transport enforces 65,536-byte JSON bodies, including streamed bodies without
Content-Length, and rejects duplicate JSON keys, non-finite constants and unsupported encodings.
Listings contain metadata, not source URLs or full requirements. Responses carry `Cache-Control:
no-store`. Errors expose only fixed codes, never raw input, source URLs, SQL, paths or traces:
OPPORTUNITIES_API_DISABLED (404), INVALID_OPPORTUNITY_INPUT (422), OPPORTUNITY_NOT_FOUND (404),
OPPORTUNITY_ID_CONFLICT / OPPORTUNITY_REVISION_CONFLICT / OPPORTUNITY_STORAGE_INTEGRITY (409),
OPPORTUNITY_PROVENANCE_CONFLICT (409, attempted change to imported source attribution),
OPPORTUNITY_REQUEST_TOO_LARGE (413), DATABASE_FAILED (503), OPPORTUNITY_OPERATION_FAILED (500).
Unsupported media/encoding and incomplete/invalid length transport errors use the existing fixed codes.

## Conflicts, privacy and verification

The UI blocks overlapping saves and never automatically retries POST/PUT or rebases edits. If a
response is lost, persistence is uncertain: form values remain, and current server state is read
separately without replacing them or claiming save success. **Check current saved version** retries
only the read. **Load current saved version (discard my edits)** explicitly loads that revision
before a new edit/save. Do not leave the page until confirmation if you need to retain your draft.

Records remain local-development data, not authenticated, encrypted or independently verified.
No content is written to browser storage or application logs. Full records stay in page memory;
bounded metadata queries have no retained cache after unmount. No real procurement or personnel
data is included in tests, and private pilot data is not accessed.

Offline tests cover all three types, URL/date validation, strict JSON and budgets, CRUD/restart,
idempotency, conflicts/concurrency, corruption, unchanged existing tables and no network/model access.
Frontend tests cover the complete form, skill edits, unsaved-change confirmation and uncertain saves.
The opt-in browser test runs create → requirements → save → reload → edit → save with synthetic data;
the profile and tender browser regressions remain in the same suite. See [workspace tests](../web-workspace.md).

[Opportunity Matching](native-matching.md) now connects saved native profiles and opportunities
through a separate explicit read-only label comparison. Opportunity CRUD remains independent.

[Pipeline](native-pipeline.md) supports separate manual acquisition tracking and stages for an
existing opportunity. Its SUBMITTED/WON/LOST values are operator declarations, not external actions
or verified facts. Creating or editing an opportunity does not add or advance pipeline entries.

The separate [TED discovery workflow](ted-discovery.md) and
[German daily catalog workflow](german-procurement-discovery.md) import selected metadata into this
same storage, preserving operator edits on repeated import. `/opportunities?opportunity_id=UUID`
opens a saved record through its authoritative detail endpoint. Imported records start with no
skills; manual edits and later comparisons/tracking remain explicit. Scraping, automatic discovery,
source synchronization, application submission, notifications, deletion, revision history and
analysis associations remain unimplemented. No qualification decision is introduced.
