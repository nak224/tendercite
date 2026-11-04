# Official German procurement discovery (DISC-1b)

At `/discover`, select **German Bekanntmachungsservice — public procurement**, explicitly load
one completed publication day, search the local catalog, inspect a notice and explicitly save it.
Open its existing Opportunities editor to edit local fields/skills, compare with a profile or
explicitly add it to Pipeline. No downstream action runs during import. TED remains a separate source.

## Verified access, formats and rights

Checked on **2026-10-31**, through the managed proxy with TLS verification:

- [Official portal](https://oeffentlichevergabe.de/).
- [Official Swagger](https://oeffentlichevergabe.de/documentation/swagger-ui/opendata/) references
  [this OpenAPI JSON](https://oeffentlichevergabe.de/documentation/api/opendata): OpenAPI 3.1.0,
  API version 1.0.0.
- [Publication policy](https://oeffentlichevergabe.de/ui/de/publicationPolicy), including its
  German/English text delivered by the official SPA. Section **4. Lizenz** states:
  “Für die Nutzung der als Open Data bereitgestellten Daten gilt die Creative Commons CC Zero-Lizenz”.
- [GovData](https://www.govdata.de/) was accessible as a catalog reference; the provider's own
  policy and each actual OCDS package establish the license used here.

The policy permits use, reuse and redistribution by interested parties and assigns **Creative
Commons CC Zero**, permitting commercial and non-commercial reuse. All 1,078 checked packages
declared `https://opendefinition.org/licenses/cc-zero/` and the official publication policy.
The adapter requires those exact declarations on every record; missing/changed declarations reject
the complete load. Retained title, buyer, description, dates, tags and identities come from that
licensed Open Data. Third-party attachments, portal account data and structured contact records
are not fetched/retained. Free-text descriptions can still contain personal information.

CC Zero does not establish accuracy, legal interpretation, bidder eligibility or endorsement,
and does not remove privacy, trademark or applicable third-party obligations. Review those before
redistribution. Notice correctness remains the publishing organization's responsibility. No model,
paid provider, marketplace, website listing scrape or restricted attachment is involved.

The documented API exposes **GET `/api/notice-exports`**, with mutually exclusive `pubDay=YYYY-MM-DD`
and `pubMonth=YYYY-MM`. It has **no documented public keyword search or pagination**. Ranges use
Europe/Berlin calendar boundaries; current/future days are rejected, availability starts at
2022-12-01, and the specification describes notices processed by midnight on the previous day.
Late processing can change later re-downloads. Peppol registered-system access and ELSTER account
features are separate and unused. No OpenAPI authentication scheme is declared; anonymous live
downloads succeeded. No numerical rate limit or maximum export size was found in the inspected
OpenAPI/policy. HTTP 429 is handled without retry.

Official formats: `eforms.zip` (original eForms-DE/EU), `ocds.zip` (OCDS 1.1, one package per
notice/version) and `csv.zip` (related tables), selected with Accept or `format` (takes precedence).
This adapter supports **daily OCDS only**, using the fixed official destination:

`https://oeffentlichevergabe.de/api/notice-exports?pubDay=2026-10-30&format=ocds.zip`

## Bounded catalog and source mapping

`services/bkms.py` downloads/parses one explicit day. Limits: **16 MiB compressed, 64 MiB expanded,
4,000 entries, 2 MiB per entry, 48 MiB normalized catalog**; 30-second HTTP inactivity / 10-second
connection timeout; one active download per process; no redirects/retries. ZIPs are read in memory,
never extracted. Unsafe paths, symlinks, encrypted entries, unsupported compression, duplicate IDs,
duplicate-key/invalid JSON, malformed known fields or mismatched IDs/URLs reject the entire export.
A legitimate empty ZIP is an explicit empty catalog. No records are silently dropped.

`repositories/bkms.py` atomically replaces one `bkms_catalog` row in the existing SQLite database.
Normal initialization adds this table without modifying existing data/tables. Failed loads retain
the previous catalog. Read-only inspection does not initialize a database. No parallel opportunity
store, archive loop, background importer or new dependency is introduced.

Local search scans retained title/buyer/description using NFKC, case-folding, whitespace normalization
and literal substring matching, ordered by source ID. It makes no outbound requests or writes and
returns 1–20 results/page. It is **not relevance-ranked**, exhaustive or a whole-service search.
The catalog includes tender, award, planning and untagged notices; source tags are displayed, never
interpreted as proof that bidding is open. The provider owns the exact completed Berlin-day boundary;
local validation rejects out-of-range/obviously future dates and maps its HTTP 400 to a safe invalid-day
error. UTC source timestamps can legitimately have the preceding calendar date; they are preserved.

Both UUID eForms IDs and numeric legacy IDs occur. Identity is notice ID **plus exact version string**
from the filename, checked against the package URI and release ID. Version padding is not guessed
equivalent. OCID is retained separately and is not assumed to identify a single notice/version.
The human link uses the service's published `/ui/notices/{id}?noticeVersion={version}` route; original
package URI remains `record_url`. References are checked against the fixed official host/identity.
The server never fetches individual records, schemas, extension URLs or attachments from packages.

The additive `native-opportunity-1` extension permits `source_origin: "bkms_import"` with
`external_source` schema **`bkms-opportunity-source-1`**: source name, original URLs, external ID,
OCID, export day, retrieval timestamp, archive/record SHA-256, CC Zero/policy references, original
allowlisted metadata and mapping warnings. Manual JSON shape and TED's source schema remain compatible.
Source metadata/references are immutable through ordinary edits; local fields remain editable.

Original title/buyer/description retain source whitespace up to 1,000/500/8,000 characters, with
`SOURCE_METADATA_SHORTENED` when needed. This is an allowlisted snapshot, not full original JSON;
hashes identify the observed originals. Native title/organization/description retain the existing
200/200/2,000 bounds with separate shortening warnings. Missing values remain null in provenance
and empty/null natively; missing titles prevent import. Only an actual tender-level
`tenderPeriod.endDate` maps to a date-only deadline. No text/lot-based inference, submission time or
legal validity is claimed; the original timestamp remains separate. Imports use PUBLIC_TENDER,
empty skills and NOT_VERIFIED. No findings, reviews, assessments, mappings or pipeline entries are created.

Identity is UUIDv5(URL namespace, canonical German publication URL), distinct from TED's namespace
input. Duplicate imports return **ALREADY_PRESENT with current local edits**, even after catalog
replacement; they never synchronize/overwrite. New versions remain distinct; unrelated manual-ID
collisions fail. New imports bind to the catalog digest and recheck it inside BEGIN IMMEDIATE.
Similar titles/buyers/OCIDs are not proof of cross-source identity. A notice also in TED may remain
separate until reliable publication links or an explicit future workflow establish equivalence.

## Setup and UI

Backend terminal, existing environment, repository root:

```powershell
$env:TENDERCITE_DATA_DIR = "data/curated/bkms-local"
$env:TENDERCITE_RETRIEVAL_ENABLED = "false"
$env:TENDERCITE_BKMS_DISCOVERY_API_ENABLED = "true"
$env:TENDERCITE_DISCOVERY_API_ENABLED = "true"
$env:TENDERCITE_OPPORTUNITIES_API_ENABLED = "true"
$env:TENDERCITE_PROFILES_API_ENABLED = "true"
$env:TENDERCITE_NATIVE_MATCHING_API_ENABLED = "true"
$env:TENDERCITE_PIPELINE_API_ENABLED = "true"
.\.venv\Scripts\python.exe -m uvicorn tendercite.main:app --host 127.0.0.1 --port 8001
```

```sh
TENDERCITE_DATA_DIR=data/curated/bkms-local TENDERCITE_RETRIEVAL_ENABLED=false TENDERCITE_BKMS_DISCOVERY_API_ENABLED=true TENDERCITE_DISCOVERY_API_ENABLED=true TENDERCITE_OPPORTUNITIES_API_ENABLED=true TENDERCITE_PROFILES_API_ENABLED=true TENDERCITE_NATIVE_MATCHING_API_ENABLED=true TENDERCITE_PIPELINE_API_ENABLED=true .venv/bin/python -m uvicorn tendercite.main:app --host 127.0.0.1 --port 8001
```

Frontend terminal (Node 24):

```powershell
$env:TENDERCITE_PROXY_TARGET = "http://127.0.0.1:8001"
npm --prefix web run dev
```

```sh
TENDERCITE_PROXY_TARGET=http://127.0.0.1:8001 npm --prefix web run dev
```

Open `http://127.0.0.1:5174/discover`. Switching sources never contacts providers; the German panel
only reads local catalog metadata. Explicitly load a completed day, inspect counts/hashes, search,
inspect the notice/original link and save it. Loading another day replaces the catalog, never saved
opportunities. Use **View Opportunity**, add skills manually, then **Compare Skills** or **Add to
Pipeline** explicitly. No profile/snapshot/AI provider is needed for discovery itself.

Source flags are independent and disabled by default. German discovery works with TED off;
native browsing/editing requires the opportunities flag. Keep both servers on loopback and use the
existing fixed development proxy. Flags are not authentication or public deployment protection.
No data is written to browser storage. Only one operation runs at a time; source switching is
disabled while pending. A lost response means persistence/replacement is **uncertain**. Read the
current catalog or inspect Opportunities before an explicit retry; no mutation is automatically
retried. A catalog conflict requires reading the current catalog and explicitly searching again.

## Local HTTP contract

Paths are under `/api/v1/discovery/bkms`, behind `TENDERCITE_BKMS_DISCOVERY_API_ENABLED`.
Shared transport enforces 65,536-byte strict JSON bodies (including streamed requests), duplicate-key
rejection, fixed privacy-safe errors and `Cache-Control: no-store`. Requests never accept paths,
URLs, database selectors, uploaded archives, months or arbitrary provider query syntax.

| Method/path | Input | Response |
| --- | --- | --- |
| GET `/catalog` | None | `bkms-catalog-summary-1` or explicit missing-catalog error |
| POST `/load` | `{"schema_version":"bkms-load-input-1","export_day":"2026-10-30"}` | Validated catalog summary; atomic replacement |
| POST `/search` | `{"schema_version":"bkms-search-input-1","catalog_id":"<returned digest>","keyword":"Software","page":1,"limit":10}` | `bkms-search-result-1`: catalog, keyword/page/limit, total_count, items, next_page |
| POST `/import` | `{"schema_version":"bkms-import-input-1","catalog_id":"<returned digest>","external_id":"10000001-1"}` (synthetic ID) | Existing `native-opportunity-save-result-1`: 201 CREATED or 200 ALREADY_PRESENT |

Summary: catalog_id, export_day, retrieved_at, archive_sha256, record_count, importable_count and
warning_count (records with any warning). Items are typed immutable BkmsSource snapshots; browser
metadata is never authoritative for import. Errors include BKMS_DISCOVERY_API_DISABLED (404),
INVALID_BKMS_INPUT (422), BKMS_REQUEST_TOO_LARGE / BKMS_EXPORT_TOO_LARGE (413),
BKMS_CATALOG_NOT_FOUND / BKMS_NOTICE_NOT_FOUND (404), BKMS_CATALOG_CONFLICT / BKMS_STORAGE_INTEGRITY /
BKMS_BUSY (409), BKMS_NOT_IMPORTABLE / BKMS_INVALID_PUBLICATION_DAY (422), BKMS_RATE_LIMITED (429),
BKMS_TIMEOUT (504), BKMS_UNAVAILABLE / BKMS_INVALID_EXPORT / BKMS_UNSUPPORTED_LICENSE (502), plus
existing safe native/database errors. No raw provider body, contact data, SQL, path or trace is echoed.
Failures are never treated as successful empty searches.

## Live observations and reproducible checks

**2026-10-31 21:46 CET**, requested day **2026-10-30**, actual implemented client: **1,078 packages**,
including **466 numeric legacy IDs** and 612 UUID IDs. ZIP: **1,815,957 bytes**; expanded metadata:
**5,171,638 bytes**. Archive SHA-256:
`61326ef9c03d248076529ae61ed4d6cb2d60623d275180e14bc71259ada8a236`.
An isolated SQLite catalog returned **15 local matches for Software**, five requested hits.
**Zero real notices were saved as native opportunities.** Warnings: 1,078 MISSING_DEADLINE,
68 SUMMARY_SHORTENED, 8 TITLE_SHORTENED, 1 SOURCE_METADATA_SHORTENED, 1 MISSING_BUYER and
1 ORGANIZATION_SHORTENED. This OCDS conversion supplied no tender-level deadlines; absence does
not establish that no legal deadline exists. Counts/hashes can change. These are connectivity and
parsing observations, not relevance/accuracy scores. Raw live artifacts remained outside Git.

The separate live smoke passed. Repeat one bounded read, outside offline CI:

```powershell
$env:TENDERCITE_LIVE_BKMS_DISCOVERY = "1"
$env:TENDERCITE_LIVE_BKMS_DAY = "2026-10-30"
.\.venv\Scripts\python.exe -m pytest tests/test_bkms_live.py -q --no-cov
```

```sh
TENDERCITE_LIVE_BKMS_DISCOVERY=1 TENDERCITE_LIVE_BKMS_DAY=2026-10-30 .venv/bin/pytest tests/test_bkms_live.py -q --no-cov
```

The controlled browser test instead uses synthetic ZIPs with actual FastAPI/SQLite/native services.
It verifies download/search/pagination/import/edit/duplicate/matching/pipeline and mobile overflow,
not live availability. Use the test-only server from the [TED guide](ted-discovery.md), with the
native/German flags above, retrieval disabled and a new temporary directory; never an operator DB:

```powershell
$env:TENDERCITE_DATA_DIR = Join-Path ([System.IO.Path]::GetTempPath()) ("tendercite-discovery-" + [guid]::NewGuid().ToString())
$env:TENDERCITE_CONTROLLED_DISCOVERY_SMOKE = "1"
$env:TENDERCITE_LLM_BASE_URL = ""
$env:TENDERCITE_LLM_MODEL = ""
.\.venv\Scripts\python.exe tests/controlled_discovery_server.py --port 8001
```

With that backend, Vite and installed Playwright Chromium, a third terminal:

```powershell
$env:E2E_BASE_URL = "http://127.0.0.1:5174"
$env:TENDERCITE_CONTROLLED_DISCOVERY_SMOKE = "1"
npm --prefix web run test:browser -- e2e/bkms-discovery.spec.ts
```

```sh
E2E_BASE_URL=http://127.0.0.1:5174 TENDERCITE_CONTROLLED_DISCOVERY_SMOKE=1 npm --prefix web run test:browser -- e2e/bkms-discovery.spec.ts
```

The harness refuses existing/non-temporary databases and retrieval/provider configuration, and has
no production provider-redirection setting. Screenshots stay in ignored test output. Offline/browser
tests use no live data, real personnel, private pilot, model downloads, paid responses or ledgers.
Remaining limits: one retained day, bounded literal search, no CPV/country filters, automatic refresh,
cross-source merging, semantic matching, inferred skills, attachment ingestion or applications.

Validation on 2026-10-31 (Python 3.12.14, Node 24.19.0): **1,650 offline Python tests passed,
3 expected live-test skips**, 96% coverage; **649 React tests passed** across 34 files.
Ruff lint/format, TypeScript, ESLint, Prettier and production build passed. Eight Chromium workflows
passed on disposable synthetic databases, including German/TED discovery and the existing native
and PDF-to-capability-report regressions. Genuine German discovery desktop/mobile screenshots
were inspected; the 390-pixel view passed an overflow check. The existing bundle-size warning
remains (541.93 kB minified JavaScript). Dependency locks, historical evaluations and ledgers are unchanged.
