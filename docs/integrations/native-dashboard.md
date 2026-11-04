# Unified dashboard and native workflow (UI-1b.11)

`/` now opens **Dashboard** at `/dashboard`. All existing `/profiles`, `/opportunities`,
`/matching`, `/pipeline` and `/tenders` routes/deep links remain available. `/discover` now adds
the separately gated [official TED search/import workflow](ted-discovery.md) and
[German procurement daily catalogs](german-procurement-discovery.md). The dashboard connects
existing workflows through read-only previews and navigation; it adds no backend API, database
table, matching algorithm or mutation.

## Start locally

Use the [standalone workspace setup](../web-workspace.md), with permitted synthetic data only.
The dashboard has no extra flag. Its three previews use independently gated native APIs, each
disabled by default. Enabling matching is optional for previews, required only for explicit comparison.

PowerShell, backend terminal in the TenderCite repository:

```powershell
$env:TENDERCITE_DATA_DIR = "data/curated/dashboard-synthetic"
$env:TENDERCITE_RETRIEVAL_ENABLED = "false"
$env:TENDERCITE_PROFILES_API_ENABLED = "true"
$env:TENDERCITE_OPPORTUNITIES_API_ENABLED = "true"
$env:TENDERCITE_PIPELINE_API_ENABLED = "true"
$env:TENDERCITE_NATIVE_MATCHING_API_ENABLED = "true"
.\.venv\Scripts\python.exe -m uvicorn tendercite.main:app --host 127.0.0.1 --port 8001
```

```sh
TENDERCITE_DATA_DIR=data/curated/dashboard-synthetic TENDERCITE_RETRIEVAL_ENABLED=false TENDERCITE_PROFILES_API_ENABLED=true TENDERCITE_OPPORTUNITIES_API_ENABLED=true TENDERCITE_PIPELINE_API_ENABLED=true TENDERCITE_NATIVE_MATCHING_API_ENABLED=true .venv/bin/python -m uvicorn tendercite.main:app --host 127.0.0.1 --port 8001
```

Frontend terminal:

```powershell
$env:TENDERCITE_PROXY_TARGET = "http://127.0.0.1:8001"
npm --prefix web run dev
```

```sh
TENDERCITE_PROXY_TARGET=http://127.0.0.1:8001 npm --prefix web run dev
```

Open `http://127.0.0.1:5174/`. Quick actions open the existing management pages; forms and save
actions remain there. No workforce server, snapshot, AI provider or model download is required.
The local feature flags provide no authentication or multi-tenancy. Production hosting remains
separate; the production build intentionally has no configured development proxy.

## Complete manual workflow

1. **Create Profile** → Profiles: explicitly create a freelancer/company profile, enter manual
   skill declarations and save. Return to Dashboard to see its name/type, without exposing skills.
2. **Add Opportunity** → Opportunities: create and save the external project/tender reference,
   organization, date-only submission deadline and required skill labels. Alternatively, **Discover
   Opportunities** → choose TED search or explicitly load/search a German daily catalog → inspect
   → explicitly save. Open the saved opportunity and add
   skills manually; importing never infers skills or creates tracking. Original TED provenance
   remains distinct from manual declarations and later local edits; German notices retain their
   own source metadata and version identity. No overlap with TED is inferred from similar text.
3. On its dashboard card or Opportunities row, **Compare this opportunity** loads the saved source
   and preselects it in Opportunity Matching. Select a saved profile and click **Compare Skills**.
   Results are normalized exact-label comparisons, not experience/eligibility verification.
4. **Track selected opportunity** opens Pipeline with the saved opportunity selected. Tracking
   is independent of matching; **Track this opportunity** also works directly from Dashboard or
   Opportunities. Click **Add to Pipeline** explicitly, or open the existing entry if already tracked.
5. Set a manual stage/priority, notes and next action/date; **Save Changes**. SUBMITTED sends
   nothing externally; WON/LOST are recorded declarations, not independently verified outcomes.
6. Return to Dashboard or reload it to read the persisted action and stage. **Open existing pipeline
   entry** opens that saved entry at its current revision. No CLI/manual UUID entry is needed.

Profile management intentionally opens its existing list; selecting a profile for comparison stays
explicit. Pipeline's existing conflict handling, retained failed-save edits, independent readback
and unsaved-navigation guard apply equally when an entry was opened through Dashboard.

## Bounded previews and independent failures

The dashboard reuses `GET /profiles`, `GET /opportunities` and `GET /pipeline`, each with
`limit=50&offset=0`. It displays up to **four** records per section in backend order. It never
walks all pages or requests aggregates. “Showing 4 of 50 loaded records” is a loaded-page count,
**not an exact dataset total**. `next_offset` indicates more records; use the full pages to browse
their existing pagination. Overdue actions or other records can be outside this bounded preview.

There is no background polling or automatic mutation. **Refresh previews** explicitly reloads the
first pages. Returning to the dashboard refetches current saved state; data has no retained cache
after unmount. Loading/error states are independent, and each failed section offers a read-only
retry. A disabled profiles API does not break Pipeline. Tender-specific feature flags are not
queried by Dashboard and cannot disable native navigation. An unconfigured proxy shows an
explanation and navigation without issuing API requests.

Profile/opportunity previews use metadata only. The existing pipeline list response can include
notes, but Dashboard immediately projects only identity, title, stage/priority, next action and
action date before caching; unrelated notes/full records are not rendered or retained there.
No profile or pipeline contents are logged or stored in localStorage/sessionStorage.

## Calendar labels and source boundaries

For active SAVED/REVIEWING/PREPARING/SUBMITTED entries, action dates before the device's local
calendar date show **Overdue action date**, equal dates show **Action date today**, and future dates
show **Upcoming action date**. Closed WON/LOST/ARCHIVED entries retain a neutral recorded-date label.
Missing dates are explicit. These comparisons infer no precise time, action completion, reminder
or notification. The labels use the device clock and refresh on render/navigation/manual refresh;
there is no midnight timer. Submission deadlines remain separate date-only source metadata.

Native opportunities are unverified external references. They are not AnalysisRun/Finding records
and have not automatically undergone document analysis. The separate **Tender Analysis** section
links to documents/findings, manual intake and existing workforce capability workflows. It does
not merge models or treat declared skills, human review or manual stages as qualification evidence.

## Contextual links

| Route | Supported selection |
| --- | --- |
| `/opportunities?opportunity_id=UUID` | Load the current saved opportunity in the existing editor, including immutable TED provenance when imported |
| `/matching?opportunity_id=UUID` | Load and preselect the saved opportunity; profile selection/comparison remain explicit |
| `/pipeline?opportunity_id=UUID` | Load and select the saved opportunity; never automatically create tracking |
| `/pipeline?pipeline_id=UUID` | Load the current existing entry/revision for inspection and explicit editing |

Only one canonical UUID parameter is accepted. Ambiguous, duplicate, unknown or malformed parameters
produce a fixed safe error without API calls. Titles, revisions, stages and other supplied query
data are never authoritative. Valid identities are read through existing typed/validated detail
APIs; missing or mismatched responses fail safely. A linked opportunity outside the first list page
is included as one explicitly loaded selection, without unbounded pagination. On a matching revision
conflict, **Refresh saved records** clears that contextual selection; reselect through the normal
paged controls or reopen the contextual link to load its current version. Outdated requests are
aborted/ignored on navigation. Contextual loading performs only GETs and never enables flags.

## Synthetic browser verification

The opt-in `web/e2e/dashboard.spec.ts` uses real FastAPI persistence with self-authored synthetic
records and Chromium. Give it its **own new empty database**, separate from existing smoke tests.
Before starting the backend above, replace its data-directory assignment:

```powershell
$env:TENDERCITE_DATA_DIR = Join-Path ([System.IO.Path]::GetTempPath()) ("tendercite-dashboard-" + [guid]::NewGuid().ToString())
```

```sh
export TENDERCITE_DATA_DIR="$(mktemp -d /tmp/tendercite-dashboard-XXXXXX)"
```

With the same backend flags, proxy and an installed Chromium, terminal 3:

```powershell
$env:E2E_BASE_URL = "http://127.0.0.1:5174"
$env:TENDERCITE_DASHBOARD_SMOKE = "1"
npm --prefix web run test:browser -- e2e/dashboard.spec.ts
```

```sh
E2E_BASE_URL=http://127.0.0.1:5174 TENDERCITE_DASHBOARD_SMOKE=1 npm --prefix web run test:browser -- e2e/dashboard.spec.ts
```

The smoke refuses existing profile/opportunity/pipeline data, creates the full native workflow
through React, reloads Dashboard, checks current backend state, and takes genuine desktop/mobile
screenshots under ignored test output. It uses no mocked retrieval/results or paid providers.
The existing tender smoke now enters `/tenders` explicitly; its workflow and deep links are preserved.

The dashboard milestone implements integration, not AI discovery, recommendations, scraping, CV extraction,
applications, emails, notifications, billing or pipeline automation. The current production build
with DISC-1b reports a nonblocking approximately 542 kB minified-chunk warning; route splitting/performance
work remains separate from this functional milestone.

Validation on 2026-10-30 (Python 3.12.14, Node 24.19.0): **579 frontend tests / 30 files passed**;
TypeScript, ESLint, Prettier and build passed (with the chunk warning above). The unchanged backend's
full offline suite passed **1,553 tests, 1 expected live-test skip**, 96% coverage; Ruff lint/format
passed. Six real Chromium flows passed on isolated synthetic databases: the complete dashboard
workflow, native matching with stale-revision refresh, pipeline persistence/unsaved Back protection,
and the existing opportunity/profile/PDF-to-capability-report regressions. Genuine desktop and
390-pixel mobile screenshots were inspected, and the mobile smoke checked for page overflow.
No private pilot, real workforce data, provider call or model download was used.
