# Official TED opportunity discovery (DISC-1a)

Open **Discover Opportunities** at `/discover`, enter a literal keyword phrase and click
**Search**. Inspect a result and its official publication, then explicitly **Save to Opportunities**.
The saved native record can be opened, manually given skill labels, compared with a saved profile
and explicitly added to Pipeline. Neither searching nor importing performs those later actions.

## Official service and scope

Checked on 2026-10-30:

- [Official Search API documentation](https://docs.ted.europa.eu/api/latest/search.html).
- Public published-notice endpoint: `POST https://api.ted.europa.eu/v3/notices/search`.
- [Current official OpenAPI specification](https://api.ted.europa.eu/api-v3.yaml), linked from
  [Swagger](https://api.ted.europa.eu/swagger).
- No API key or paid service is required for public published-notice searches.

The existing `services/ted.py` client and its documented parsing/URL checks are reused. Discovery
adds a fixed active-competition search, separate from the existing evaluation PDF/XML acquisition
workflow. Example provider query: `(FT ~ "Software") AND (form-type = competition) SORT BY publication-date DESC`,
with `scope: "ACTIVE"`, `paginationMode: "PAGE_NUMBER"` and `checkQuerySyntax: false` (true would
validate syntax without executing). The literal phrase cannot contain expert-query delimiters,
wildcards or arbitrary URLs. This first UI exposes no country, CPV or date filters.

Discovery requests these documented fields: `publication-number`, `notice-title`, `official-language`,
`publication-date`, `notice-type`, `form-type`, `classification-cpv`, `links`, `buyer-name`,
`description-proc`, `deadline-receipt-tender-date-lot`. Title/buyer/description prefer an official
notice language; a fallback is possible and is not evidence of an original German document.

Search is explicit, with 1–20 results per page (UI choices 5/10/20), pages 1–50, a 4 MiB decoded
provider-response ceiling, 30-second HTTP timeout / 10-second connect timeout and **one attempt**
per operation. Redirects are disabled. The destination is fixed; browser input cannot select a
host. Normal HTTPX proxy/CA handling remains enabled. There is no website scraping, attachment
fetching, proxy bypass, background polling, exhaustive crawl or automatic retry.

TED totals and page order are observations of a changing index. Stateless page-number navigation
can duplicate or miss notices as publications change. `pagination_capped` identifies totals beyond
the local 50-page window. This is not an exhaustive procurement dataset or a relevance assessment.

## Local configuration

From the repository, with the existing Python environment and Node 24 installed, backend terminal:

```powershell
$env:TENDERCITE_DATA_DIR = "data/curated/discovery-local"
$env:TENDERCITE_RETRIEVAL_ENABLED = "false"
$env:TENDERCITE_DISCOVERY_API_ENABLED = "true"
$env:TENDERCITE_OPPORTUNITIES_API_ENABLED = "true"
# Optional, for explicit downstream comparison/tracking and local manual profiles:
$env:TENDERCITE_PROFILES_API_ENABLED = "true"
$env:TENDERCITE_NATIVE_MATCHING_API_ENABLED = "true"
$env:TENDERCITE_PIPELINE_API_ENABLED = "true"
.\.venv\Scripts\python.exe -m uvicorn tendercite.main:app --host 127.0.0.1 --port 8001
```

```sh
TENDERCITE_DATA_DIR=data/curated/discovery-local TENDERCITE_RETRIEVAL_ENABLED=false TENDERCITE_DISCOVERY_API_ENABLED=true TENDERCITE_OPPORTUNITIES_API_ENABLED=true TENDERCITE_PROFILES_API_ENABLED=true TENDERCITE_NATIVE_MATCHING_API_ENABLED=true TENDERCITE_PIPELINE_API_ENABLED=true .venv/bin/python -m uvicorn tendercite.main:app --host 127.0.0.1 --port 8001
```

Frontend terminal:

```powershell
npm --prefix web ci
$env:TENDERCITE_PROXY_TARGET = "http://127.0.0.1:8001"
npm --prefix web run dev
```

```sh
npm --prefix web ci
TENDERCITE_PROXY_TARGET=http://127.0.0.1:8001 npm --prefix web run dev
```

Open `http://127.0.0.1:5174/discover`. Discovery is **disabled by default**; its flag controls
search and explicit import. The independent opportunities flag permits browsing/editing saved
records. No retrieval/model/LLM configuration is needed. The fixed Vite proxy remains development
only; keep both servers on loopback. Flags are not authentication or public deployment protection.

## Local HTTP contracts

Both endpoints use strict JSON, reject duplicate keys/non-finite values/unknown fields and accept
at most 65,536 bytes, including streamed bodies without Content-Length. Unsupported content types
and encodings are rejected. Responses use `Cache-Control: no-store`.

| POST under `/api/v1` | Request | Response |
| --- | --- | --- |
| `/discovery/ted/search` | `{"schema_version":"ted-search-input-1","keyword":"Software","page":1,"limit":10}` | `ted-search-result-1`, source TED, scope ACTIVE_COMPETITION, keyword/page/limit/total_notice_count/items/next_page/pagination_capped |
| `/discovery/ted/import` | `{"schema_version":"ted-import-input-1","publication_id":"99999991-2099"}` (fictional ID) | Existing `native-opportunity-save-result-1`: 201 CREATED or 200 ALREADY_PRESENT, with current record |

Search is read-only and does not require repository access. Each item contains publication ID,
source, title, organization, summary, publication_date, submission_deadline, official source_url,
retrieved_at, importable and explicit warnings. Missing optional metadata remains null in search
and the corresponding native empty/null representation on import. A missing title prevents import;
no replacement title is invented. Native bounds are 200 title/buyer characters and 2,000 summary
characters; any shortening is flagged and the original publication remains linked.

Only a single distinct lot submission date maps to the native date-only deadline. Several lot
dates produce `MULTIPLE_TENDER_DEADLINES` and no chosen deadline. Missing dates produce
`MISSING_DEADLINE`. A date establishes neither submission time/time zone nor legal applicability.
Dates are not shifted through UTC. Always inspect the publication and relevant lot before acting.

Fixed safe failures include `DISCOVERY_API_DISABLED` (404), `INVALID_DISCOVERY_INPUT` (422),
`DISCOVERY_REQUEST_TOO_LARGE` (413), `TED_TIMEOUT` (504), `TED_RATE_LIMITED` (429),
`TED_UNAVAILABLE` / `TED_INVALID_RESPONSE` (502), `TED_NOTICE_NOT_FOUND` (404),
`TED_NOTICE_NOT_IMPORTABLE` (422), and existing opportunity/database errors. They never expose
provider bodies, source content, SQL, database paths or exception traces. Failure is never returned
as a successful zero-result search. No mutation is automatically retried. A lost import response
means persistence is uncertain: inspect Opportunities before an explicit retry.

## Native persistence, attribution and duplicates

Import accepts only the publication ID and re-fetches current official metadata; it does not trust
browser-supplied titles or source URLs. It uses the existing native opportunity table, transactions
and result contracts. **No migration or parallel discovery database** is introduced.

The backward-compatible `native-opportunity-1` extension adds `source_origin: "ted_import"` and
`external_source` with schema `ted-opportunity-source-1`, source TED, canonical `external_id`,
`original_url`, publication_date, retrieved_at and mapping_warnings. Manual records retain
`source_origin: "manual_operator_entry"` and omit the absent metadata field when serialized.
Original TED provenance is immutable through normal native updates. The editable local reference
URL can change without overwriting the original URL. Ordinary manual creation cannot forge TED
origin. The opportunity editor visibly distinguishes these origins.

Deduplication uses UUIDv5 with URL namespace and `https://ted.europa.eu/notice/{canonical ID}`.
Leading-zero publication padding and display-language URL differences do not create new records.
`BEGIN IMMEDIATE` and the existing opportunity primary key serialize concurrent imports. Reimport
returns the **current** saved record, even after edits, without re-fetching or overwriting it.
An unrelated manual record colliding with the derived identity causes a conflict, not relabeling.
There is no automatic source synchronization or immutable opportunity revision history.

Only explicit metadata maps: title, buyer labels, summary, publication/date-only deadline, source
TED and official URL; type is PUBLIC_TENDER. Imported skills are empty. Users may manually add
skills in the existing editor before comparing. No requirements, evidence quotations, findings,
reviews, approvals, capability mappings or pipeline entries are created. Verification stays
`NOT_VERIFIED`. Source attribution proves neither legal interpretation nor bidder qualification.

## Rights, privacy and local storage

TED documents public reuse of the Search API, but availability is not a blanket license for every
notice field or linked document. Review the [TED legal notice](https://ted.europa.eu/en/legal-notice)
and [reuser information](https://ted.europa.eu/en/simap/developers-corner-for-reusers), attribution,
personal-data obligations and any notice-specific restrictions before redistribution. During this
session those two portal pages returned HTTP 202, empty bodies and `x-amzn-waf-action: challenge`; their complete
current terms could **not** be reviewed. No restrictions were bypassed and no claim of unrestricted
licensing is made. The official API documentation/specification and metadata API were accessible.

Only explicitly selected metadata is persisted locally; no source PDFs or third-party attachments
are downloaded by this feature. Original links require an explicit browser click. Browser state
is in memory, not localStorage/sessionStorage; raw provider bodies and user data are not logged.
Use an isolated ignored `data/curated/` directory. No private pilot, real personnel, paid response,
annotation or cost ledger is involved in automated tests. No AI or paid provider is called.

## Verification: live versus controlled

**Actual live read-only check, 2026-10-30 15:09:41 UTC:** the implemented client executed two
pages for `Software`, limit 2, ACTIVE competition scope. Each returned two records and TED reported
32,567 matches. Page 1: publications 753063-2026 and 753103-2026; page 2: 753346-2026 and
753792-2026. The first page had one TITLE_SHORTENED warning; the second had one MISSING_DEADLINE.
No real notice was imported, no PDFs were downloaded and no accuracy/coverage score was measured.
These IDs/counts document connectivity and metadata parsing at that instant, not stable fixtures.
Raw live responses were kept only in temporary local files, outside Git.

Opt-in live smoke (two free metadata requests, separate from offline CI):

```powershell
$env:TENDERCITE_LIVE_TED_DISCOVERY = "1"
.\.venv\Scripts\python.exe -m pytest tests/test_discovery_live.py -q --no-cov
```

```sh
TENDERCITE_LIVE_TED_DISCOVERY=1 .venv/bin/pytest tests/test_discovery_live.py -q --no-cov
```

The browser test instead uses **controlled synthetic TED HTTP responses with actual FastAPI and
SQLite persistence**. It proves explicit search/pagination/import, duplicate preservation of edited
skills/title, native editor navigation, exact-label comparison and pipeline tracking. It does not
prove live connectivity. The test-only server refuses existing databases, non-temporary locations,
retrieval/provider configuration and missing opt-in. Production has no configurable provider bypass.

To reproduce, replace the backend launch above with the following, retaining the native feature
flags, clearing LLM configuration and leaving retrieval disabled. Use a fresh directory each run:

```powershell
$env:TENDERCITE_DATA_DIR = Join-Path ([System.IO.Path]::GetTempPath()) ("tendercite-discovery-" + [guid]::NewGuid().ToString())
$env:TENDERCITE_CONTROLLED_DISCOVERY_SMOKE = "1"
$env:TENDERCITE_LLM_BASE_URL = ""
$env:TENDERCITE_LLM_MODEL = ""
.\.venv\Scripts\python.exe tests/controlled_discovery_server.py --port 8001
```

```sh
TENDERCITE_DATA_DIR="$(mktemp -d /tmp/tendercite-discovery-XXXXXX)" TENDERCITE_CONTROLLED_DISCOVERY_SMOKE=1 TENDERCITE_RETRIEVAL_ENABLED=false TENDERCITE_LLM_BASE_URL='' TENDERCITE_LLM_MODEL='' TENDERCITE_DISCOVERY_API_ENABLED=true TENDERCITE_OPPORTUNITIES_API_ENABLED=true TENDERCITE_PROFILES_API_ENABLED=true TENDERCITE_NATIVE_MATCHING_API_ENABLED=true TENDERCITE_PIPELINE_API_ENABLED=true .venv/bin/python tests/controlled_discovery_server.py --port 8001
```

Start the same Vite proxy, then in another terminal with installed Playwright Chromium:

```powershell
$env:E2E_BASE_URL = "http://127.0.0.1:5174"
$env:TENDERCITE_CONTROLLED_DISCOVERY_SMOKE = "1"
npm --prefix web run test:browser -- e2e/discovery.spec.ts
```

```sh
E2E_BASE_URL=http://127.0.0.1:5174 TENDERCITE_CONTROLLED_DISCOVERY_SMOKE=1 npm --prefix web run test:browser -- e2e/discovery.spec.ts
```

Use `PLAYWRIGHT_CHROMIUM_EXECUTABLE` only if selecting an installed system Chromium. Genuine
screenshots are generated under ignored `web/test-results/`. Ordinary pytest/Vitest tests use
synthetic responses without network or models. No second provider, semantic search, automatic
import/synchronization, notifications or submission workflow is implemented.

Validation on 2026-10-30, Python 3.12.14 / Node 24.19.0: **1,604 offline Python tests passed,
2 expected live-test skips**, 96% coverage; Ruff lint/format passed. **622 React tests passed**
across 32 files; TypeScript, ESLint, Prettier and production build passed. The existing bundle-size
warning remains (524.21 kB minified JavaScript). Seven Chromium workflows passed on disposable
synthetic databases: controlled discovery, dashboard, profiles, opportunities, native matching,
pipeline and PDF intake through capability reporting. The actual controlled discovery screenshot
was visually inspected. The separate opt-in live TED smoke also passed. These checks are not
procurement accuracy, interpretation or qualification measurements.
