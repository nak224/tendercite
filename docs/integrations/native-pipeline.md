# Native opportunity pipeline (UI-1b.10)

**Pipeline** at `/pipeline` records manual progress pursuing an existing external opportunity.
It is local acquisition tracking, not a full CRM or an application-submission service.

[Dashboard](native-dashboard.md) shows bounded pipeline previews and current action dates. Its
existing-entry link loads the authoritative record/revision; a track-opportunity link only selects
the saved opportunity and still requires **Add to Pipeline**. Matching remains optional.

## Local setup and complete manual workflow

Use the [standalone workspace setup](../web-workspace.md) with permitted synthetic records only.
The independent `TENDERCITE_PIPELINE_API_ENABLED` flag is **false by default** and provides no
authentication, account isolation or production-readiness guarantee. Opportunity selection uses
the existing opportunities API flag. Profile/matching flags below are optional for tracking but
enable the full create → compare → track workflow.

PowerShell, TenderCite backend terminal:

```powershell
$env:TENDERCITE_DATA_DIR = "data/curated/pipeline-synthetic"
$env:TENDERCITE_RETRIEVAL_ENABLED = "false"
$env:TENDERCITE_OPPORTUNITIES_API_ENABLED = "true"
$env:TENDERCITE_PIPELINE_API_ENABLED = "true"
$env:TENDERCITE_PROFILES_API_ENABLED = "true"
$env:TENDERCITE_NATIVE_MATCHING_API_ENABLED = "true"
.\.venv\Scripts\python.exe -m uvicorn tendercite.main:app --host 127.0.0.1 --port 8001
```

```sh
TENDERCITE_DATA_DIR=data/curated/pipeline-synthetic TENDERCITE_RETRIEVAL_ENABLED=false TENDERCITE_OPPORTUNITIES_API_ENABLED=true TENDERCITE_PIPELINE_API_ENABLED=true TENDERCITE_PROFILES_API_ENABLED=true TENDERCITE_NATIVE_MATCHING_API_ENABLED=true .venv/bin/python -m uvicorn tendercite.main:app --host 127.0.0.1 --port 8001
```

Frontend terminal, using the existing fixed local proxy:

```powershell
$env:TENDERCITE_PROXY_TARGET = "http://127.0.0.1:8001"
npm --prefix web run dev
```

```sh
TENDERCITE_PROXY_TARGET=http://127.0.0.1:8001 npm --prefix web run dev
```

1. Optionally create a freelancer/company profile with manually declared skills in **Profiles**.
2. Create and explicitly save the external project/tender reference in **Opportunities**.
3. Optionally use **Opportunity Matching** to compare saved skill labels. A MATCHED declaration
   is no decision to pursue, proof of qualification or automatic tracking trigger.
4. Open **Pipeline**, select an existing opportunity and click **Add to Pipeline**. It starts at
   SAVED, priority MEDIUM, with empty notes/action and no action date. Already tracked opportunities
   offer **Open existing pipeline entry**; adding again never resets their progress.
5. Open the entry, edit stage, priority, notes and next action/date, then **Save Changes**. Wait for
   server confirmation. Reload and reopen the entry to inspect persistence.
6. Filter the list by stage. All stages includes archived entries. Filtering or reloading the list
   retains a separately open editor, including unsaved edits. Lists/selectors page through 50 records.

Navigation between Opportunities, Opportunity Matching and Pipeline uses their existing top-level
routes; no unsupported selected-record deep links are assumed. Opportunity titles, organization,
type and submission deadline are read from the current native opportunity, not copied into pipeline
storage. Edit those source details in Opportunities and refresh/reopen to read them again. No extra
opportunity, tender analysis, workforce snapshot or profile owner is created by tracking.

## Stages and dates

| Stage | Manual meaning |
| --- | --- |
| SAVED | Track this opportunity |
| REVIEWING | Consider whether to pursue |
| PREPARING | Prepare an application, proposal or bid |
| SUBMITTED | Operator records an external submission; TenderCite submits nothing |
| WON | Operator records a successful outcome, not independently verified |
| LOST | Operator records an unsuccessful outcome, not independently verified |
| ARCHIVED | No longer actively tracked; remains inspectable/filterable |

Users may move between any stages to correct their records. No rigid sequence, inference or
automatic transition exists. Priority is LOW, MEDIUM or HIGH. Notes are bounded to 4,000 characters,
next action to 1,000; empty strings clear them. The optional next-action date uses `YYYY-MM-DD`
or JSON null to clear it. It is an operator follow-up date, **distinct from the opportunity's
submission deadline**. Both are date-only values, not precise times; no timezone, exact cutoff,
reminder or calendar event is inferred. There is no qualification or Go/No-Go judgment.

## Versioned API and SQLite consistency

All endpoints are under `/api/v1/pipeline`, independently gated by the pipeline flag:

| Method | Path | Contract/result |
| --- | --- | --- |
| POST | `/pipeline` | PipelineCreate → 201 CREATED or 200 ALREADY_PRESENT |
| GET | `/pipeline?limit=50&offset=0&stage=SAVED` | PipelineListing; optional stage filter |
| GET | `/pipeline?opportunity_id=UUID` | Same listing scoped to one opportunity; useful for uncertain creation readback |
| GET | `/pipeline/{pipeline_id}` | PipelineEntry |
| PUT | `/pipeline/{pipeline_id}` | PipelineUpdate → UPDATED; expected revision required |

Synthetic creation (use the actual saved opportunity UUID):

```json
{
  "schema_version": "native-pipeline-create-1",
  "pipeline_id": "40000000-0000-0000-0000-000000000001",
  "opportunity_id": "30000000-0000-0000-0000-000000000001"
}
```

Synthetic update:

```json
{
  "schema_version": "native-pipeline-update-1",
  "expected_revision": 1,
  "values": {
    "stage": "PREPARING",
    "priority": "HIGH",
    "notes": "Synthetic manual declaration, not an external fact.",
    "next_action": "Draft the synthetic proposal locally",
    "next_action_due_date": "2027-02-15"
  }
}
```

`native-pipeline-save-result-1` returns status and an `entry`. `native-pipeline-entry-1` contains
`record` (`native-pipeline-record-1`: pipeline/opportunity UUIDs, revision, creation/update timestamps
and values) plus current `OpportunityMetadata`. `native-pipeline-listing-1` contains entries and
`next_offset`; limits are 1–100 with deterministic pipeline-UUID ordering. Unexpected fields,
versions, enum values, malformed dates and nonpositive/out-of-range revisions are rejected.

One additive `native_pipeline` SQLite table stores only pipeline identities/values, with a unique
opportunity UUID, foreign key to existing `native_opportunities`, revision/stage/payload constraints
and a stage/identity index. No existing table is migrated destructively. Both create and update
use **BEGIN IMMEDIATE**, check current state inside the transaction and serialize writes. Updates
also constrain the SQL statement by expected revision. Reads load pipeline and current opportunity
metadata in one consistent read transaction. Corrupt identities, revisions or payloads fail closed.

Repeated creation for an already tracked opportunity returns its **current** entry, even after
progress changes or if another creation UUID was supplied. It never clears fields or changes its
revision. Reusing an occupied pipeline UUID for a different opportunity is an ID conflict. Missing
opportunities are rejected before creation; database uniqueness prevents concurrent duplicates.
No imported snapshots, profiles, analyses, reviews, mappings or assessments are modified.

The current record is stored with an incrementing revision; there is no immutable stage history,
multi-profile ownership, deletion or complex activity timeline in this MVP.

## Failures, privacy and unsaved changes

The shared transport bounds bodies to **65,536 bytes**, including streamed requests without
Content-Length, and rejects duplicate JSON keys, unsupported encodings and non-finite values.
Responses use `Cache-Control: no-store`. Browser requests omit credentials and reject write
redirects; no arbitrary origin, database selector, filesystem path or URL is accepted. No API key,
external provider, model download, email or network integration is involved.

Safe error codes include PIPELINE_API_DISABLED (404), OPPORTUNITY_NOT_FOUND / PIPELINE_NOT_FOUND
(404), PIPELINE_ID_CONFLICT / PIPELINE_REVISION_CONFLICT / PIPELINE_STORAGE_INTEGRITY /
OPPORTUNITY_STORAGE_INTEGRITY (409), INVALID_PIPELINE_INPUT (422), PIPELINE_REQUEST_TOO_LARGE (413),
DATABASE_FAILED (503) and PIPELINE_OPERATION_FAILED (500), plus existing fixed transport codes.
No raw body, private field names, SQL, database path or exception trace appears in error messages.

Writes are explicit and never automatically retried. Overlapping actions are blocked. A lost or
invalid response may have persisted: the UI keeps edits, does not claim success and reads current
state separately. **Check current saved entry** performs only a read. **Load current saved entry
(discard my edits)** explicitly adopts that version, with confirmation for dirty edits; manually
reapply any desired changes before another save. Conflicts never silently rebase or overwrite.
Uncertain creation also checks the selected opportunity for an existing entry before any manual retry.

Internal record switches require discard confirmation. Leaving through SPA navigation or browser
Back/Forward uses React Router's built-in blocker; reload/close uses the browser's beforeunload
warning where supported. Keep editing or explicitly discard; pending requests must finish before
SPA navigation is allowed. No localStorage/sessionStorage draft recovery is provided. Private
notes/results stay in page/request memory (query cache is discarded on unmount), not browser
persistent storage or application logs. Keep the local database and generated artifacts private.

## Synthetic browser verification

Use **another fresh empty disposable data directory** for `web/e2e/pipeline.spec.ts`, separate from
the existing opportunity/profile/tender smokes and native-matching smoke. Replace the backend's
data-directory assignment above with a fresh path before starting it:

```powershell
$env:TENDERCITE_DATA_DIR = Join-Path ([System.IO.Path]::GetTempPath()) ("tendercite-pipeline-" + [guid]::NewGuid().ToString())
```

```sh
export TENDERCITE_DATA_DIR="$(mktemp -d /tmp/tendercite-pipeline-XXXXXX)"
```

Keep the same flags/backend command and frontend proxy. With Chromium installed, terminal 3:

```powershell
$env:E2E_BASE_URL = "http://127.0.0.1:5174"
$env:TENDERCITE_PIPELINE_SMOKE = "1"
npm --prefix web run test:browser -- e2e/pipeline.spec.ts
```

```sh
E2E_BASE_URL=http://127.0.0.1:5174 TENDERCITE_PIPELINE_SMOKE=1 npm --prefix web run test:browser -- e2e/pipeline.spec.ts
```

The smoke refuses nonempty opportunity/pipeline lists. It creates an original synthetic opportunity,
adds it explicitly, changes stage/action, checks unsaved Back navigation, saves, reloads, filters and
verifies duplicate prevention and unchanged source opportunity data. Screenshots remain ignored.
These checks establish local workflow behavior, not real acquisition outcomes or human verification.

Automatic discovery, recommendations, submissions, synchronization, notifications, calendar/CRM
integration, team assignment, revenue forecasting and multi-user accounts remain separate work.
No later milestone is implemented here.

Validation on 2026-10-30 (Python 3.12.14, Node 24.19.0): **1,553 offline Python tests passed,
1 expected live-test skip**, 96% coverage; Ruff lint/format passed. React **541 tests / 26 files**,
TypeScript, ESLint, Prettier and production build passed. Five genuine Chromium flows passed:
the new pipeline smoke, native matching with stale-version refresh, and the existing opportunity,
profile and complete PDF-to-capability-report regressions, using separate fresh databases where
required. The matching smoke's sidebar locator was scoped after new contextual links made it
ambiguous. The pipeline's genuine desktop screenshot was inspected. No private pilot/real personnel
records, paid requests or model downloads were used.
