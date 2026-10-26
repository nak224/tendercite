# Manual requirement-to-skill mapping in React (UI-1b.5b)

**Skill Mapping** at `/tenders/skill-mapping` uses the merged PR #27 contracts. PR #28 supplies
the independent React workspace; Team Capacity Engine and PostgreSQL are not runtime dependencies.
An analysis detail also links to the view with its analysis ID preselected. No matching algorithm,
backend contract, database schema or dependency is changed.

## Local synthetic setup

Use an explicitly permitted, disposable synthetic TenderCite database containing an analysis,
reviewed findings and an imported schema 1.0 snapshot. Do not use real personnel records or the
private BfS pilot. From the TenderCite repository, in the backend terminal:

```powershell
$env:TENDERCITE_DATA_DIR = "data/curated/mapping-ui-synthetic"
$env:TENDERCITE_RETRIEVAL_ENABLED = "false"
$env:TENDERCITE_CAPABILITY_MAPPING_API_ENABLED = "true"
.\.venv\Scripts\python.exe -m uvicorn tendercite.main:app --host 127.0.0.1 --port 8001
```

```sh
TENDERCITE_DATA_DIR=data/curated/mapping-ui-synthetic TENDERCITE_RETRIEVAL_ENABLED=false TENDERCITE_CAPABILITY_MAPPING_API_ENABLED=true .venv/bin/python -m uvicorn tendercite.main:app --host 127.0.0.1 --port 8001
```

Start the frontend in another terminal:

```powershell
$env:TENDERCITE_PROXY_TARGET = "http://127.0.0.1:8001"
npm --prefix web run dev
```

```sh
TENDERCITE_PROXY_TARGET=http://127.0.0.1:8001 npm --prefix web run dev
```

Open http://127.0.0.1:5174. Keep the servers local. The mapping flag is disabled by default and
is independent of `TENDERCITE_CAPABILITY_SNAPSHOT_API_ENABLED`, which is required separately if
importing the fixture through the existing import page. Neither flag authenticates users or
establishes synthetic provenance. No browser-selected origin, CORS workaround or production
authentication guarantee is added.

## Operator workflow

1. Select an **Analysis** and an **Imported snapshot**, then **Load mapping overview**. Analyses
   are discovered from `GET /findings`; there is no analysis-list HTTP API. Only analyses with
   current findings are discoverable. The snapshot selector requests at most 50 metadata records,
   displays the exact hash/assessment period and never silently chooses a different snapshot.
2. Inspect the scoped current requirements, review/grounding states, mapping states and catalog.
   Select one **Requirement** to inspect original/effective text, quotes, physical source-page
   links and mapping details. Catalog entries show names, categories and UUIDs; no individual
   workforce records, assignments or capacity rows are returned/displayed.
3. A source-grounded CONFIRMED/MODIFIED finding with a current review event can be prepared for
   mapping. This is an interpretation/source gate, **not** a claim that every eligible requirement
   is skill-related. Deadlines, insurance, finance or certificate conditions may need other evidence.
   UNREVIEWED/REJECTED or ungrounded findings remain inspectable with no write controls.
4. For **NOT_FOUND**, explicitly select 1–100 catalog skills, enter a nonblank rationale (maximum
   2000 Unicode characters) and optionally a non-personal alias (`a-z`, `A-Z`, digits, `_`, `-`,
   maximum 64). Only **ALL_OF** is supported. Choose **Review proposed mapping**. This sends no
   mutation: inspect the requirement, exact scope, UUIDs, rationale and alias first. Edits clear
   the proposal; no skills are suggested or inferred.
5. Choose **Create DRAFT**. Only the existing MappingSelection body is sent. The server's record
   is checked against the proposal, including scope, skills and review binding. The authoritative
   overview is then reloaded. The current mapping ID, revision, UUIDs and **DRAFT** state appear.
   Draft creation never approves anything.
6. For a current **DRAFT**, enter a separate approval rationale/alias, check the explicit approval
   acknowledgement and choose **APPROVE mapping**. Approval submits the exact displayed mapping
   ID/revision plus current review event/digest. The server also checks the draft's stored binding.
   A successful response and fresh overview show **APPROVED**, an operator association only.
7. Reload after later reviews or changes in another window. A new review event invalidates an
   old approval even if the reviewed words/digest remain identical; the backend returns **STALE**.
   STALE and REVOKED mappings are read-only here. Do not substitute an older approval or a mapping
   from another snapshot. Revision/revocation/history and matching/reporting remain separate CLI
   or later workflows; no reactivation action is provided.

Reloads reset unsaved proposal/approval form state while retaining the selected finding. Mapping
writes and manual overview reloads are blocked during a mutation and its authoritative reload.
Cached data after a failed refresh is marked potentially stale and cannot authorize writes until
an explicit reload succeeds. No automatic POST retries or approvals occur.

## Exact HTTP contract and review races

All requests use the existing same-origin `/tendercite-api/api/v1` proxy. Credentials are omitted,
mutation redirects rejected, JSON is bounded to 65,536 bytes and only fixed error codes are shown.

| Action | Existing endpoint / contract |
| --- | --- |
| Snapshot choices | `GET /capability-snapshots?limit=50` → `LISTED` metadata |
| Scoped state | `GET /capability-workflow/overview?analysis_id=...&snapshot_hash=...` → `capability-workflow-overview-1` |
| Create draft | `POST /capability-mappings/drafts` → `MappingSelection`, response `CapabilityMappingRecord` / DRAFT |
| Explicit approve | `POST /capability-mappings/{mapping_id}/approve` → `MappingApprovalInput`, response APPROVED |

Draft JSON fields are `schema_version: capability-workflow-mapping-selection-1`, `analysis_id`,
`finding_id`, `snapshot_hash`, `operator: ALL_OF`, canonical unique `skill_ids`, `rationale` and
`operator_alias` (string or null). **No expected-review fields exist in this request.** The backend
captures its current binding during preparation and rechecks it transactionally before persistence.
If a review changed since the browser preview, a draft can already be stored against that changed
review before the UI detects the mismatched response. The UI cannot guarantee an atomic browser
preview-to-create condition; it flags uncertainty and reloads. Inspect the current requirement
and saved association before any separate approval.

Approval JSON fields are `schema_version: capability-mapping-approval-1`, `mapping_id`,
`expected_revision`, `expected_review_event_id`, `expected_review_digest`, `rationale` and
`operator_alias`. Values come from the freshly loaded overview, never manual revision/digest
entry. A draft originally bound to an older review cannot be silently rebound by approval; the
existing backend rejects it. Some old drafts need CLI revision before they can be approved.

HTTP 409 conflicts such as `MAPPING_REVISION_CONFLICT`, `REQUIREMENT_REVIEW_CONFLICT` or
`REQUIREMENT_NOT_ELIGIBLE`, invalid catalog errors and transport/response failures all lead to
authoritative reload. A failed or lost response **does not prove nothing was persisted**. The UI
does not claim a saved mutation without a matching confirmation and does not retry automatically.
It can show a persisted draft/approval from the subsequent GET while retaining the uncertain
request warning. Unknown error text, JSON field names, exception details and database paths are
not displayed. A disabled API shows `CAPABILITY_MAPPING_API_DISABLED`; enabling it requires an
explicit backend configuration change/restart, not a browser action.

## Verification and evidence limits

Run `npm --prefix web run typecheck`, `lint`, `format:check`, `test` and `build` as described in the
[workspace guide](../web-workspace.md). The focused suites are `src/api/capabilityMappings.test.ts`
and `src/pages/SkillMapping.test.tsx`; they use synthetic fixtures and mocked fetch only. Existing
upload, intake, source-page, review, assessment and snapshot-import tests remain in the full suite.

The opt-in browser test in `web/e2e/standalone.spec.ts` now includes explicit draft/approval and
STALE after an identical-text CONFIRM. Use a **new disposable data directory**, retrieval/provider
configuration disabled, and both snapshot/mapping flags explicitly enabled. Set `E2E_BASE_URL`,
`TENDERCITE_SYNTHETIC_SMOKE=1` and optionally `PLAYWRIGHT_CHROMIUM_EXECUTABLE`, then run
`npm --prefix web run test:browser`. The test generates its own PostgreSQL-related PDF and uses
the existing synthetic snapshot. Its simulated reviews and approvals are not genuine human
verification; screenshots stay in ignored test output. No real workforce/private tender data,
provider request, model download, automatic mapping or matching report is involved.

Source occurrence, reviewed interpretation, approved operator association and independent
qualification are distinct. This page establishes only the explicit mapping decision and its
review/snapshot binding. It does not prove experience years, certification, professional capacity,
bidder eligibility or legal compliance, and does not change Go/No-Go assessments automatically.
See the [feature inventory](capability-feature-status.md) for the remaining milestones.
