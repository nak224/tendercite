# Capability Matching in the standalone workspace (UI-1b.6b)

Open **Capability Matching** at `/tenders/capability-report`. The page sends the existing
`AssessmentSelection` to `POST /api/v1/capability-workflow/report` only when **Run Capability
Matching** is clicked. The existing backend UUID-membership matcher and manually approved,
review-bound mappings supply every conclusion; no matching algorithm runs in the browser.

## Local setup and independent opt-ins

Use the [standalone setup](../web-workspace.md) and its existing fixed Vite proxy. TenderCite
runs independently of Team Capacity Engine. Keep both servers on loopback, with an isolated
synthetic database; do not use private pilot or real workforce data for testing.

PowerShell backend settings (restart FastAPI after changing flags):

```powershell
$env:TENDERCITE_DATA_DIR = "data/curated/capability-ui-demo"
$env:TENDERCITE_RETRIEVAL_ENABLED = "false"
$env:TENDERCITE_CAPABILITY_SNAPSHOT_API_ENABLED = "true"
$env:TENDERCITE_CAPABILITY_MAPPING_API_ENABLED = "true"
$env:TENDERCITE_CAPABILITY_REPORT_API_ENABLED = "true"
.\.venv\Scripts\python.exe -m uvicorn tendercite.main:app --host 127.0.0.1 --port 8001
```

The flags are separate, disabled-by-default local-development safeguards, **not authentication**:
snapshot validation/import, snapshot listing/mapping operations, and private report computation.
The report API needs only `TENDERCITE_CAPABILITY_REPORT_API_ENABLED`. Snapshot discovery uses
`TENDERCITE_CAPABILITY_MAPPING_API_ENABLED` and lists at most 50 imported metadata records. If
that discovery is disabled or unavailable, enter the full existing `sha256:...` value in **Exact
snapshot hash**. No default/latest snapshot is substituted; the report backend verifies existence.

In terminal 2:

```powershell
$env:TENDERCITE_PROXY_TARGET = "http://127.0.0.1:8001"
npm --prefix web run dev
```

Open `http://127.0.0.1:5174`. On Linux, use the same environment names with shell assignments
and `.venv/bin/python`. Clear provider configuration for synthetic testing; neither retrieval
nor LLM access is needed. No browser field selects a backend origin.

## Complete manual workflow without CLI operations

1. **PDF:** upload a permitted synthetic PDF on Documents & findings; inspect stored source pages.
2. **Requirements:** manually enter statements and exact quotations in Manual requirements;
   validate, explicitly confirm and save. Findings start UNREVIEWED. No extraction is inferred.
3. **Review:** open each finding, inspect its quotation and explicitly CONFIRM/MODIFY/REJECT its
   interpretation. Quote occurrence and human interpretation are different evidence layers.
4. **Snapshot:** import a permitted synthetic schema 1.0 artifact in Import snapshot, with an
   explicit acknowledgement. A compatible external producer supplies it; no producer server
   is required at TenderCite runtime.
5. **Skill Mapping:** select the analysis and exact snapshot, manually select catalog UUIDs
   using ALL_OF, inspect the proposal, create DRAFT and explicitly APPROVE separately. No skills
   are suggested or inferred. See the [mapping guide](manual-skill-mapping-ui.md).
6. **Capability Matching:** select that analysis and snapshot, explicitly check the desired
   findings, then click Run Capability Matching. Analysis detail also links to this page with
   its analysis preselected. Nothing is automatically checked. At most **100** findings can be
   selected. An explicit empty selection returns an empty, zero-count report, never "all".

Analysis discovery reuses current findings; there is no analysis-list API and analyses without
findings are not discoverable here. Findings are loaded for the exact selected analysis and
include review status. Unreviewed/rejected findings can be selected for an explicit NOT_EVALUATED
result; selecting them does not make them eligible. Invalid scopes are rejected, not repaired.

## Reading results safely

The summary displays the backend's selected/current-approved/draft/stale/revoked/unmapped and
not-evaluated counts. Requirement rows show effective text, original/source details, review and
grounding states, mapping state/revision/UUIDs, required catalog skills, recorded coverage and
non-evaluation reasons. Links open existing finding/review and physical PDF source-page views.

Only current eligible approved mappings contribute. DRAFT, STALE, REVOKED and absent mappings
remain visible with NOT_EVALUATED and the backend's reasons; no historical approval is substituted.
A new review invalidates an old approval even for identical wording. Revision/revocation/history
still use the CLI; this page neither approves nor repairs mappings.

Individual aggregate counts are visible first. Expand **Individual recorded-skill coverage**
to render per-person records: pseudonymous UUID, recorded coverage, matched skills and missing
recorded assignments. No names/emails are displayed and people are not ranked. Missing records
do not prove missing skills. Backend evidence and snapshot limitations remain visible.
Qualification stays NOT_VERIFIED; capacity NOT_EVALUATED; reviewed meaning NOT_ASSESSED. Nothing
here verifies experience years, certificates, bidder eligibility, legal compliance, reserved
capacity or a Go/No-Go decision. The separate manual Go/No-Go matrix is unchanged.

## Errors, privacy and remaining limits

The client checks report/schema/algorithm versions, exact analysis and snapshot identity, exact
selected finding set, catalog/source/mapping structure and coverage record shapes. Mismatched or
malformed responses are rejected. Disabled API errors expose the fixed
CAPABILITY_REPORT_API_DISABLED code; unavailable discovery still allows the exact-hash fallback.
Raw backend bodies, field names and exception text are not shown.

Report POSTs use the fixed local proxy, `credentials: omit`, `cache: no-store` and redirect
rejection. There are no automatic retries. Duplicate clicks are blocked while computing.
Changing analysis, snapshot/hash or finding selection clears results, aborts an outstanding
request and ignores its late response. Navigating away discards component state. Reports never
enter browser storage, application logs or TanStack query/mutation caches. Discovery queries
have no retained cache after unmount. This does not claim forensic memory erasure or control
over browser developer tools. Treat pseudonymous UUIDs and source text as private records.

Results are point-in-time: explicitly run again after reviews/mappings change. No report is
persisted or automatically refreshed. Large snapshots can still produce large synchronous
responses; individual details are not rendered until expanded. There is no report pagination,
download/export action, profile matching, candidate ranking, team selection or automatic verdict.

The opt-in [synthetic browser smoke](../web-workspace.md) covers generated PDF → intake → simulated
review → snapshot import → explicit mapping/approval → approved coverage → new simulated review
→ STALE/NOT_EVALUATED. It uses disposable data, not genuine human verification or real personnel.
See the [HTTP contract](capability-report-http.md) and [feature inventory](capability-feature-status.md).
