# Standalone TenderCite React workspace (ARCH-1a)

`web/` is TenderCite's independent React/TypeScript/Vite application. It migrates the tested
TenderCite screens from Team Capacity Engine, with the same API paths, analysis/document/finding
identities and `/tenders` deep links. Team Capacity Engine, PostgreSQL and its frontend are not
installation or runtime dependencies. Its original screens remain an untouched fallback.

## Install and start locally

Use Python 3.12 and Node.js **24.x**. The existing Python setup/locks in the README remain valid;
an already installed TenderCite environment needs no new Python dependencies. For a fresh
core-only environment (no retrieval models or Streamlit required):

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
npm --prefix web ci
```

```sh
python3.12 -m venv .venv
.venv/bin/python -m pip install -e .
npm --prefix web ci
```

The core-only commands resolve platform-specific Python dependencies; the checked-in hashed
Python locks are the reproducible Linux CPU/dev alternative used in CI. `web/package-lock.json`
pins the independent frontend graph. No Team Capacity checkout, generated OpenAPI schema,
database connection or export service is used by this package.

From the **TenderCite repository**, start the backend in terminal 1. Use only permitted local
synthetic data for this unauthenticated development workspace; the example directory is ignored:

```powershell
$env:TENDERCITE_DATA_DIR = "data/curated/react-synthetic"
$env:TENDERCITE_RETRIEVAL_ENABLED = "false"
.\.venv\Scripts\python.exe -m uvicorn tendercite.main:app --host 127.0.0.1 --port 8001
```

```sh
TENDERCITE_DATA_DIR=data/curated/react-synthetic TENDERCITE_RETRIEVAL_ENABLED=false .venv/bin/python -m uvicorn tendercite.main:app --host 127.0.0.1 --port 8001
```

In terminal 2, start the independent frontend:

```powershell
$env:TENDERCITE_PROXY_TARGET = "http://127.0.0.1:8001"
npm --prefix web run dev
```

```sh
TENDERCITE_PROXY_TARGET=http://127.0.0.1:8001 npm --prefix web run dev
```

Open **http://127.0.0.1:5174**. `/` redirects to `/tenders`. Instead of a session variable,
you may copy `web/.env.example` to ignored `web/.env`; restart Vite after changing the target.
There is exactly one fixed development proxy, `/tendercite-api/api/v1` → `/api/v1`, and the
existing 180-second upload/parsing inactivity timeouts. No `/api`/`/health` Team Capacity proxy,
browser URL selector, credential forwarding or CORS workaround is introduced. Without explicit
configuration the UI shows an unavailable state and makes no TenderCite requests.

For snapshot import, **explicitly opt in on the TenderCite backend** before starting/restarting it:

```powershell
$env:TENDERCITE_CAPABILITY_SNAPSHOT_API_ENABLED = "true"
```

```sh
export TENDERCITE_CAPABILITY_SNAPSHOT_API_ENABLED=true
```

The flag remains false by default. Use it only with permitted synthetic records. Obtain a local
JSON artifact from Team Capacity Engine or another compatible schema 1.0 producer; no configured
workforce server or hardcoded export link is needed. The existing consumer contract, including
its `source_system: team-capacity-engine` marker and fixed limitations, is unchanged; arbitrary
producer schemas/source identifiers are not accepted. Do not relabel incompatible data to force
an import. See [snapshot HTTP contract](integrations/capability-snapshot-http.md).

## Available UI and remaining boundaries

| Workflow | React workspace | Remaining limitation |
| --- | --- | --- |
| Native manual profiles | `/profiles`: freelancer/company CRUD, manual skills and keyword preferences | Separate profiles API flag; no snapshot dependency or profile matching; [guide](integrations/native-profiles.md) |
| Native manual opportunities | `/opportunities`: external opportunity references, skill labels, explicit saves and unsaved-edit confirmation | Separate opportunities API flag; no discovery, source fetching or analysis/profile dependency; [guide](integrations/native-opportunities.md) |
| Documents | Explicit multi-PDF upload, list, metadata and source-page text | No embedded PDF viewer; retrieval/indexing stays disabled unless separately configured |
| Analyses/findings | Current findings, known-ID analysis lookup, source navigation | No analysis-list API; analyses without findings need a known ID |
| Manual intake | Source selection, exact quotations, validate, explicit idempotent commit | One citation editable per requirement; all new findings remain UNREVIEWED |
| Human interpretation review | CONFIRM/MODIFY/REJECT, effective/original values, history and authoritative refresh | No authenticated reviewer identity or optimistic-concurrency guarantee |
| Manual assessment | Five statuses, required rationale, eligible/ineligible groups and readback | Reviews can invalidate an assessment; no automatic overall Go/No-Go verdict |
| Snapshot validation/import | Original local JSON bytes, metadata, acknowledgement, INSERTED/ALREADY_PRESENT | 16 MiB bound; API flag disabled by default; no automatic synchronization |
| Workforce snapshot generation | Optional external producer responsibility | Not implemented or moved into TenderCite |
| Snapshot metadata selection and manual skill mapping | Bounded snapshot selector, scoped overview/catalog, explicit ALL_OF proposal, DRAFT and separate APPROVE | Local mapping flag required; no inference or qualification proof |
| Full snapshot history/export, mapping revision/revocation/history | Existing local CLI workflows | No revision/revocation/history UI yet |
| Capability assessment reports | [Capability Matching](integrations/capability-matching-ui.md): explicit analysis/snapshot/finding selection and backend report | Separate report flag; manual hash fallback; private pseudonymous details collapsed by default |
| Model-based search/analysis and result exports | Existing backend/legacy Streamlit workflows | Not added to React by this migration; model/provider access and transmission permissions remain separate |
| Evaluation/annotations/paid pilots | Existing separate local evaluation tools | Not bridged into production findings or invoked by React |

All mutations stay explicit, without automatic retries; interrupted/invalid responses may have
persisted. Keep the selected artifact/draft, inspect authoritative state and retry only explicitly.
The existing 1 MiB intake and 16 MiB snapshot boundaries, strict metadata/identity checks,
independent source validation and review invalidation behavior are preserved.

A verified quotation proves source occurrence, not interpretation correctness. Human review,
manual assessment, operator skill association and independent qualification are different evidence
levels. No status or internal skill assignment automatically establishes legal compliance, years
of experience, certification, bidder eligibility, reserved capacity or contractual availability.
No LLM, embedding download or analysis starts on page load. Raw source-bearing data remains in
page/request memory, without new browser persistent storage or logging.

[UI-1b.5b manual skill mapping](integrations/manual-skill-mapping-ui.md) adds the existing mapping
HTTP workflow after the original ARCH-1a migration. It requires
`TENDERCITE_CAPABILITY_MAPPING_API_ENABLED=true` on the local synthetic backend; this flag remains
off by default and is independent of snapshot import. See the guide for proposal inspection,
approval revision/review bindings, safe uncertain outcomes and current feature inventory.

## Verification

```powershell
npm --prefix web run typecheck
npm --prefix web run lint
npm --prefix web run format:check
npm --prefix web test
npm --prefix web run build
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\ruff.exe check .
.\.venv\Scripts\ruff.exe format --check .
```

Linux uses the same npm commands and `.venv/bin/python` / `.venv/bin/ruff`. Python dev dependencies
(the dev lock, or `.[dev]`) are required for pytest/Ruff; the core-only install above runs the app
but does not install those tools. The existing Python CI and Docker job are unchanged.
A separate small React workflow installs only the locked Node
package and runs types/lint/format/tests/build. Python Docker images exclude `web/` and continue
serving the existing backend/Streamlit setup; React is not silently bundled into that image.

Opt-in genuine browser smokes are in `web/e2e/standalone.spec.ts`, `web/e2e/profiles.spec.ts`
and `web/e2e/opportunities.spec.ts`.
The profile test creates five manual skills, saves, reloads, edits/removes/adds skills and saves
again using only native profile endpoints. Enable `TENDERCITE_PROFILES_API_ENABLED=true` for it;
no snapshot is required. The opportunity test covers manual external references, skill edits,
save/reload/edit/save and canceled unsaved-change confirmation, using only opportunity endpoints.
Enable `TENDERCITE_OPPORTUNITIES_API_ENABLED=true` for it. Run all three tests together only
against fresh disposable data.
For an independence check, stop Team Capacity Engine's frontend and PostgreSQL backend.
Point TenderCite to a **new empty disposable
directory**, not any private/pilot database. Disable retrieval and provider configuration; enable
snapshot, mapping, report, profiles and opportunities flags explicitly. The tests refuse nonempty
document/finding, profile or opportunity lists before their respective writes, but that alone does not establish ownership
of a database. Use a fresh disposable directory.

Install Chromium once if needed (`cd web; npx playwright install chromium`), start the two
TenderCite servers as above, then in terminal 3:

```powershell
$env:E2E_BASE_URL = "http://127.0.0.1:5174"
$env:TENDERCITE_SYNTHETIC_SMOKE = "1"
npm --prefix web run test:browser
```

```sh
E2E_BASE_URL=http://127.0.0.1:5174 TENDERCITE_SYNTHETIC_SMOKE=1 npm --prefix web run test:browser
```

`PLAYWRIGHT_CHROMIUM_EXECUTABLE` can select an already installed browser. The test generates an
original PDF, creates/reviews a synthetic intake, saves/reads back a manual assessment, checks
source-page navigation, validates/imports a synthetic snapshot twice, creates and explicitly
approves a manual mapping, inspects approved recorded coverage, then checks STALE/NOT_EVALUATED
after a subsequent identical-text review. It sends twelve explicit writes and two explicit read-only
report computations; there are no automatic POST retries. Simulated reviews are
test actions, not genuine human verification. Screenshots/failure outputs stay in ignored
`web/test-results/`; remove only the owned disposable data after stopping its backend.

ARCH-1a verification used Node 24.19.0, Python 3.12.14 and an installed Chromium with a fresh
synthetic SQLite directory. All 228 frontend tests passed, as did the full 1,262-test backend suite
(one explicitly opt-in live test skipped), Ruff and the frontend checks/build. The opt-in browser
journey above passed with Team Capacity's frontend stopped, no PostgreSQL processes/containers
and no listeners on its usual 5173/8000/5432 ports. It exercised nine explicit mutations and a
deduplicated snapshot repeat; no provider/model was invoked. The first browser attempt stopped
on a test-only select locator; it was corrected to use the accessible combobox role and rerun
against another fresh database. These are synthetic checks, not real tender accuracy or genuine
human review. Desktop visual inspection used the genuine resulting screenshot; mobile and
operator-specific environments still need independent verification before retiring the fallback.

## Migration safety and later cleanup

`frontend/app.py` is unchanged and remains the legacy Streamlit client (port 8501, backend default
8000); it can explicitly target port 8001 via `TENDERCITE_API_URL`. React keeps existing deep links
but has its own process, shell, package and port. A production build/preview intentionally has
no configured development integration. Production hosting, reverse proxy, authentication and
tenancy are not solved here. Inside a container, loopback identifies the container, not the host;
configure a reachable private origin explicitly if adapting this local setup.

Source and adaptation provenance: [web/MIGRATION.md](../web/MIGRATION.md). Team Capacity Engine's
original screens, proxy, tests, backend and workforce export must stay intact until this PR passes
checks and an operator independently verifies the main workflows. Only a later authorized Team
Capacity cleanup PR may remove its TenderCite screens/clients/routes/proxy/docs and corresponding
tests. It must retain that application's workforce/project/planning features and snapshot producer,
with appropriate regressions. No database cleanup or two-way synchronization belongs to this migration.
