# Multi-day German procurement discovery (DISC-2a)

At `/discover`, choose **German Bekanntmachungsservice — public procurement**. Explicitly
load completed publication days, inspect the retained coverage, search/filter locally and save
a chosen notice to Opportunities. No source, month, background job or automatic retention is added.
TED remains independent. Imported opportunities still support manual edits, comparison and Pipeline.

## Storage, limits and upgrading

- Retain **at most seven distinct export days** and **96 MiB of active normalized catalog data**.
  This is selected coverage, potentially with gaps, not all German procurement notices.
- An ordinary load adds a day and preserves every other retained day. A repeated day is rejected
  before download. **Replace stored day → Confirm day replacement** explicitly re-downloads that
  date using its expected current catalog digest. An outdated digest causes a conflict.
- At seven days, explicitly **Remove YYYY-MM-DD → Confirm day removal** before adding another.
  There is no expiry, oldest-day eviction or silent deletion. A byte-budget failure also preserves
  the previously committed state; removing a day can free active storage.
- Per-download limits remain **16 MiB compressed, 64 MiB expanded, 4,000 records, 2 MiB per record,
  48 MiB per normalized day**. HTTP inactivity/connect timeouts remain 30/10 seconds, with one
  active download per process, no redirects and no automatic retries. ZIPs are never extracted.
- `bkms_catalog_days` stores one validated payload/digest per date in the existing SQLite database.
  Writes use `BEGIN IMMEDIATE`; conflicts, quota checks, replacement/removal and migration commit
  together. Failed downloads/transactions do not replace a previously retained catalog.

An existing singleton `bkms_catalog` remains readable after upgrading, including read-only access
before initialization. On the first explicit German catalog write/import, its exact payload and
digest are copied to the daily table in the same transaction. The original table is renamed
`bkms_catalog_legacy` and retained as an inactive recovery copy. No operator data is rewritten.
Explicit removal does **not** reactivate this copy on restart. Corrupt/conflicting legacy catalogs
produce a safe integrity error and remain preserved; ordinary native opportunities remain independent.

The 96 MiB limit covers active catalog payloads. The inactive legacy copy can retain another
48 MiB, and SQLite file/index/free-page overhead is additional. Day removal is not secure erasure
or a privacy purge: imported source snapshots, the legacy copy and SQLite pages can still retain
data. No automatic backup deletion or vacuum is performed. Keep the whole local data directory private.

## Search, categories and versions

Search scans only retained title, buyer and description (the existing allowlisted/truncated
metadata). Literal NFKC/case-folded/whitespace-normalized phrase matching is unchanged. There is
no remote keyword API call, attachment download or semantic ranking. Pagination is 1–20 hits per
page, at most 28,000 stored records. Results sort by actual source publication timestamp descending,
then export day descending, then exact external ID. Missing timestamps are last, never invented.

The [official OCDS 1.1 releaseTag codelist](https://github.com/open-contracting/standard/blob/1.1/schema/codelists/releaseTag.csv)
was checked on **2026-11-04**. Exact, case-sensitive groups are:

| Filter | Codes |
| --- | --- |
| Tender-related | `tender`, `tenderAmendment`, `tenderUpdate`, `tenderCancellation` |
| Award-related | `award`, `awardUpdate`, `awardCancellation` |
| Planning-related | `planning`, `planningUpdate` |
| Other / unknown | Missing tags, unknown codes, tags from multiple groups, or other OCDS codes such as `contract` |

All supplied tags must belong to one group for that classification. Raw tags remain visible.
Updates/cancellations are included: **tender-related does not mean open for bidding**. Categories
are publication metadata, not eligibility or procurement-state determinations.

Exact publication ID **plus exact version string** is the search identity. Repeated identities
across days appear once, using the newest retained export day's complete snapshot. Every occurrence
retains its day, catalog digest and original record SHA-256. Different raw hashes are flagged;
fields are never merged. Full alternate snapshots remain in their respective daily catalogs.

By default, only the highest **numeric version among retained versions** of each notice is shown.
This reduction happens **before keyword/category filtering**. An earlier matching text can therefore
be hidden; choose **Show all stored notice versions** to inspect it. Versions are separate results
and native opportunities. Equal numeric labels such as `01` and `1` remain distinct identities and
are both shown; no padding equivalence is guessed. “Highest stored” never claims provider currency.
No title/buyer/OCID similarity or TED overlap is treated as cross-source identity.

An explicit save binds to the displayed snapshot's **daily** digest, not a client-supplied metadata
object. Existing native imports return ALREADY_PRESENT with current operator edits and their original
immutable provenance, even after source replacement/removal. A new version does not overwrite them.

## Source access, license and dates

The [original source guide](german-procurement-discovery.md) documents verified CC Zero terms,
fixed official URLs, anonymous access, supported OCDS/CSV/eForms export formats and privacy limits.
Those checks and per-record license guards remain unchanged. Official OpenAPI was re-read on
2026-11-04; daily export, rather than an invented remote search endpoint, remains the integration.

A separate bounded **live** read for export day **2026-11-03** obtained **1,021 records**:
597 with `tender`, 348 with `award`, five with `planning` and 71 without tags. Archive SHA-256:
`ef6ab507a181bd95eb92deef4d895d2a1619caf2b01590f9f046ea2254a40c7d`.
This verifies connectivity/parsing and observed tags, not search accuracy. No live notice was saved
to native Opportunities. Raw live artifacts stay outside Git. Counts can change on re-download.

Export day is the provider's Europe/Berlin processing boundary; source publication timestamp and
local retrieval timestamp are separate. A preceding UTC publication date can be legitimate. Missing
deadlines remain **Unavailable in OCDS**. No date is inferred from text, lots, publication or retrieval.
Explicit [official eForms deadline enrichment](official-deadline-enrichment.md) now provides a separate, verified-modern-schema source view. Original OCDS/imported deadline values are not changed.

## Local setup and HTTP contract

Use the existing [PowerShell/Linux backend and Vite commands](german-procurement-discovery.md#setup-and-ui).
`TENDERCITE_BKMS_DISCOVERY_API_ENABLED=true` remains independently required and disabled by default.
Keep servers on loopback with the fixed development proxy; this is not authentication or public
deployment readiness. No new setting, database selector, URL input or dependency is introduced.

Paths are under `/api/v1/discovery/bkms`. All use `Cache-Control: no-store`, existing 64 KiB bounded
strict JSON parsing and safe errors. Metadata reads/search make no writes or provider requests.

| Method/path | Request | Response |
| --- | --- | --- |
| GET `/catalogs` | None | `bkms-catalog-collection-1`: collection_id, newest-first days with catalog summary/warning counts, stored_bytes, max_days=7, max_storage_bytes=100663296; empty collection is explicit |
| POST `/load` | `{"schema_version":"bkms-load-input-1","export_day":"2026-10-30"}` | Existing `bkms-catalog-summary-1` |
| POST `/load` (replace) | Same plus `"expected_catalog_id":"<current daily digest>"` | Existing summary; only that day is replaced |
| POST `/remove` | `{"schema_version":"bkms-remove-input-1","export_day":"2026-10-30","expected_catalog_id":"<current daily digest>"}` | Updated collection |
| POST `/search` | `{"schema_version":"bkms-search-input-2","collection_id":"<current collection digest>","keyword":"Software","page":1,"limit":10,"notice_category":"ALL","include_earlier_versions":false}` | `bkms-search-result-2`: coverage, echoed query, count/page, hits with source/category/version/occurrences, next_page |
| POST `/import` | Existing v1 input with displayed occurrence's `catalog_id` and exact `external_id` | Existing native CREATED/ALREADY_PRESENT result |

Collection identity hashes the ordered date/daily-digest scope, so changes invalidate pagination;
clients must read current metadata and explicitly search again. The old GET `/catalog` still returns
the newest **export day**, and v1 `/search` still searches its explicitly selected daily digest.
`bkms-catalog-1` and `bkms-opportunity-source-1` are unchanged. Repeated `/load` without an expected
daily digest now returns `BKMS_DAY_ALREADY_STORED` (409), preventing implicit replacement.
Other new errors: `BKMS_DAY_LIMIT_REACHED` (409), `BKMS_TOTAL_STORAGE_LIMIT` (413).
Existing conflicts, rate limits, invalid export/license, missing catalog and unavailable errors remain.

No load/remove/save is automatically retried. A lost response means the outcome is uncertain:
read retained days or inspect Opportunities before an explicit retry. The UI keeps the selected
source locked during operations and rejects stale reads superseded by a catalog change.

## Controlled browser check

Use a **fresh disposable** temporary SQLite directory, never an operator/pilot database. The
test-only server returns self-authored ZIPs for 2026-10-30 (six records) and 2026-10-31 (three),
including a duplicate, new version, award and untagged notice. It never contacts live providers.
Enable the source/native flags in the source guide, disable retrieval and clear LLM configuration.

PowerShell backend terminal, repository root:

```powershell
$env:TENDERCITE_DATA_DIR = Join-Path ([System.IO.Path]::GetTempPath()) ("tendercite-discovery-" + [guid]::NewGuid().ToString())
$env:TENDERCITE_CONTROLLED_DISCOVERY_SMOKE = "1"
$env:TENDERCITE_RETRIEVAL_ENABLED = "false"
$env:TENDERCITE_LLM_BASE_URL = ""
$env:TENDERCITE_LLM_MODEL = ""
.\.venv\Scripts\python.exe tests/controlled_discovery_server.py --port 8001
```

Start Vite in another terminal with the fixed target `http://127.0.0.1:8001` as in the source guide.
With an installed Playwright Chromium, a third terminal:

```powershell
$env:E2E_BASE_URL = "http://127.0.0.1:5174"
$env:TENDERCITE_CONTROLLED_DISCOVERY_SMOKE = "1"
npm --prefix web run test:browser -- e2e/bkms-multiday.spec.ts
```

```sh
E2E_BASE_URL=http://127.0.0.1:5174 TENDERCITE_CONTROLLED_DISCOVERY_SMOKE=1 npm --prefix web run test:browser -- e2e/bkms-multiday.spec.ts
```

Each browser scenario needs its own fresh database. The controlled workflow exercises two retained
days, cross-day deduplication, pagination, type/version filters, explicit save/edit, replacement and
removal with native provenance retained. Existing German and TED browser scenarios exercise
duplicate imports, skill comparison and Pipeline separately. Screenshots remain ignored local files.
Offline tests additionally cover singleton upgrade/rollback, seven-day/byte limits and concurrency.

Verified on **2026-11-04**, Python **3.12.14**, Node **24.19.0**: **1,666 Python tests passed,
three opt-in live tests skipped**, 96% coverage; **658 React tests passed** across 35 files.
Ruff lint/format, TypeScript, ESLint, Prettier and production build passed. **Nine Chromium
workflows passed** with controlled synthetic providers and disposable real SQLite databases.
Desktop and 390-pixel multi-day screenshots were inspected, including expanded duplicate provenance;
the mobile overflow check passed. Build retains the existing large-chunk warning (550.39 kB minified
JavaScript). This is local functional verification, not search-quality or procurement eligibility
evaluation. Dependency locks, historical evaluations, private pilot data and ledgers are unchanged.

Remaining limits: bounded local literal metadata search, selected dates only, no inferred deadlines,
CPV/country filters, automated refresh, attachments, cross-source merging, AI matching or applications.
