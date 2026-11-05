# Official deadline enrichment (DISC-2b)

German Discovery can explicitly download **one retained day's official eForms export** and show
submission-deadline metadata beside its exact OCDS notice/version. Existing Opportunities, source
snapshots and operator edits are never updated by enrichment. No eligibility or bidding-state decision
is made. TED and seven-day local search retain their existing behavior.

## Source verification before implementation

Checked on **2026-11-05**, using the managed proxy and verified TLS:

- [Official OpenAPI](https://oeffentlichevergabe.de/documentation/api/opendata) documents anonymous
  `GET /api/notice-exports?pubDay=YYYY-MM-DD&format=eforms.zip`. It describes original eForms notices,
  one file per notice, the same notices in OCDS/eForms exports, Berlin publication-day boundaries
  and eForms-DE 2.1's EU SDK 1.13/1.14 bases. No remote keyword endpoint was invented.
- [Official publication policy, section 4](https://oeffentlichevergabe.de/ui/de/publicationPolicy):
  “Für die Nutzung der als Open Data bereitgestellten Daten gilt die Creative Commons CC Zero-Lizenz”.
  Its German/English text was inspected in the official SPA asset `index-Bpwxq_Px.js` referenced by
  that page. It covers data supplied as Open Data, including these original eForms exports; reuse
  of the allowlisted IDs, lots and deadline values is covered. XML does not embed a per-record license:
  the stored CC Zero reference records this policy assessment. Privacy/third-party duties still apply.
- The documented field paths were checked in the official EU SDK's
  [1.13.0 fields](https://github.com/OP-TED/eForms-SDK/blob/1.13.0/fields/fields.json) and
  [1.14.0 fields](https://github.com/OP-TED/eForms-SDK/blob/1.14.0/fields/fields.json), and namespaces in
  its [UBL ContractNotice schema](https://github.com/OP-TED/eForms-SDK/blob/1.13.0/schemas/maindoc/UBL-ContractNotice-2.3.xsd).
  These paths are identical in both bases and were observed in the actual eForms-DE 2.1 sample.

| Business term | Exact path (root `/*`, UBL `cbc`/`cac` namespaces) |
| --- | --- |
| BT-701 notice identity | `/*/cbc:ID[@schemeName='notice-id']` |
| BT-757 version | `/*/cbc:VersionID` |
| BT-137 lot identity | `/*/cac:ProcurementProjectLot[cbc:ID/@schemeName='Lot']/cbc:ID` |
| BT-131(d) tender receipt date | `/*/cac:ProcurementProjectLot[cbc:ID/@schemeName='Lot']/cac:TenderingProcess/cac:TenderSubmissionDeadlinePeriod/cbc:EndDate` |
| BT-131(t) tender receipt time | Same period, `cbc:EndTime` |

No fuzzy identity, padded-version equivalence, ordinal inference, lot merging or alternative date
fallback is used. Participation-request periods (BT-1311), invitation periods and other dates are
**not** submission deadlines for this adapter. National business-rule/full-XSD validation is not
implemented: this is a bounded extraction of documented UBL fields from a verified schema subset.

### Actual bounded sample

Requested completed day **2026-11-03**, once per format. eForms ZIP: **3,829,280 bytes**, expanded
**21,140,282 bytes**, largest XML **632,899 bytes**, **1,021 records**. All filenames matched their
XML root ID/version exactly and the OCDS identity set exactly, including distinct `01`/`1` labels.

- eForms archive SHA-256: `ffa1d363a5d41e7e04f6cff1dec43e1de63c2185f232df3913228ec6104b1289`
- OCDS archive SHA-256: `ef6ab507a181bd95eb92deef4d895d2a1619caf2b01590f9f046ea2254a40c7d`
- Customizations: 546 `eforms-de-2.1`, 473 `eforms-sdk-0.1`, two `eforms-sdk-1.0`.
- Actual deadline periods: 355 in 201 modern notices and 354 in legacy 0.1 notices. Lot-specific
  values have date/time offsets. Two modern notices contain inconsistent date/time offsets between
  `+01:00` and `+02:00`; they are conflicts, not repaired timestamps.

The implemented adapter and isolated SQLite integration produced: **166 SINGLE, 33 MULTIPLE_LOTS,
344 MISSING, two CONFLICT, 476 UNRECOGNIZED**. The last group includes the 475 legacy-schema records
and one unsupported Part structure. Legacy deadlines observed during inspection are deliberately
not supported without separate documented mapping verification. Missing means absent at supported
paths, not legally absent. The normalized metadata was **301,949 bytes**. A local `Software` query
returned 20 matches/20 hits, each with exact-snapshot enrichment. **Zero real native opportunities
were imported.** These are source/parsing observations, not accuracy, legal correctness or open-bid
measurements. Provider exports can change. Raw source artifacts are never committed or retained in
application tables; only the necessary allowlisted metadata/digests are persisted.

## Extraction and deadline states

Initial supported customization: **`eforms-de-2.1`**, with exact known UBL ContractNotice,
ContractAwardNotice or PriorInformationNotice roots. Other customization versions remain explicitly
UNRECOGNIZED, even when similar XML paths contain dates. Prefix spelling is irrelevant; namespaces,
container paths and identities must match. Unsupported roots/malformed XML reject the entire load.

- **SINGLE**: one supported lot's one source period. A date without time stays date-only; no
  midnight/default time is supplied. Missing time/offsets have explicit warnings.
- **MULTIPLE_LOTS**: multiple lot records, retained individually, even if values happen to agree.
  Missing lot deadlines also remain explicit. No earliest/latest deadline is selected.
- **MISSING**: no tender-submission deadline at supported paths. Consult the original publication.
- **CONFLICT**: repeated nonrepeatable values/periods, duplicate lot IDs or inconsistent supplied
  date/time offsets. Original bounded values remain visible; no timestamp is synthesized.
- **UNRECOGNIZED**: unsupported schema, path, Part structure, invalid calendar/time or unsupported
  lexical form. No reliable interpreted deadline is asserted.

Source date and time strings retain their offsets/precision. No named timezone is guessed from an
UTC offset, no timezone is added, and values are not silently converted into a browser-local date.
The supported subset uses four-digit calendar dates and `HH:mm[:ss[.ffffff]]` times with optional
XSD offsets up to 14 hours. Other lexical forms remain unrecognized. SINGLE does not establish
full schema validity, agreement between OCDS and eForms snapshots, legal correctness or current
procurement state. Both source formats remain separate and visible; discrepancies require operator
inspection of the original publication.

## Persistence, limits and failures

`bkms_deadline_days` is an additive table in the existing SQLite database: one hashed normalized
payload per retained day, foreign-keyed to `bkms_catalog_days`. It stores exact identity/version,
lot/period values, statuses/warnings, customization/extraction versions, retrieval time, original
XML/archive digests and policy references. It stores no XML, titles, buyer contacts or unrelated fields.
The original daily OCDS payload and native opportunity source contract remain unchanged.

The same bounded official downloader is reused: **16 MiB compressed, 64 MiB expanded, 4,000 files,
2 MiB per XML**, 30-second HTTP inactivity/10-second connect timeout, no redirects or retries. XML
requires UTF-8; DTDs, entities, non-declaration processing instructions and XInclude are rejected.
Parsing stops at **64 levels / 30,000 elements per record**. No referenced schema, entity or URL is
fetched. Only scalar date/time children are recognized. Per record: at most **128 lots**, **four
periods per lot**, **four date/time values per period**, **40 characters per value**. Unsupported
budgets/invalid archives fail atomically. ZIPs are inspected in memory, never extracted.

Metadata is bounded to **8 MiB per day / seven rows (56 MiB)**, additional to the catalog's 96 MiB
active budget and possible inactive singleton recovery copy. SQLite overhead is additional.
Enrichment verifies the exact complete identity set, selected daily digest and expected prior
enrichment digest inside `BEGIN IMMEDIATE`. A day changed during download, concurrent reload, bad
identity, unsafe source or database failure cannot partially replace the metadata/catalog.

Replacing an OCDS day makes its previous enrichment **STALE**, even if source identities happen to
agree. Stale metadata remains inspectable but is omitted from search hits until an explicit reload.
There is no cross-day/version fallback: a deduplicated hit uses its representative day's exact
snapshot. **Explicit removal of a retained day also removes its enrichment** via the foreign key;
no other expiry/automatic deletion is added. Imported opportunities and their original provenance
remain intact. This is not secure erasure of SQLite pages/backups.

## UI and local HTTP contract

Use the [existing local setup and source flag](german-procurement-discovery.md#setup-and-ui):
`TENDERCITE_BKMS_DISCOVERY_API_ENABLED=true`, disabled by default. No new source/credentials,
background requests or production-security claims are introduced.

1. Select German Discovery and explicitly load a completed OCDS day.
2. Choose **Load official deadlines for YYYY-MM-DD** for a retained day. This reads the current
   local metadata summary, then explicitly downloads that day's official eForms export.
3. Inspect the returned state/counts; explicitly search again to see source values for each hit.
4. Use **Read stored deadlines** to inspect metadata without provider access, including after an
   uncertain response. Search also reads stored values only. No download runs on page load/search.
5. Inspect each lot, conflict/warning, extraction provenance and original-publication link.

Paths under `/api/v1/discovery/bkms` use existing 64 KiB strict bounded request parsing, safe fixed
errors, no paths/URLs/database selectors, no-store responses and the shared single-download guard.

| Method | Input | Result |
| --- | --- | --- |
| GET `/deadlines/{catalog_id}` | Exact retained daily digest | `bkms-deadline-summary-1`: NOT_LOADED/CURRENT/STALE, active/bound digests, export day, enrichment/archive digests, retrieval/extraction version, status counts |
| POST `/deadlines/load` | `{"schema_version":"bkms-deadline-load-1","catalog_id":"<daily digest>"}`; include `expected_enrichment_id` from summary when reloading | Same summary after complete atomic validation/persistence |
| POST `/search` (v2) | Existing combined search request | Additive nullable `deadline_enrichment` on each returned hit: exact scope/provenance and allowlisted notice/lot values |

Old daily/v1 search remains unchanged. Existing native import returns its existing CREATED or
ALREADY_PRESENT result. Enrichment does **not** supply a new opportunity deadline: the original
OCDS value is still imported, and existing operator edits are retained. The UI labels source and
server-confirmed local dates separately. There is no automatic adoption or new adoption endpoint.
Any operator choosing a local date must explicitly edit/save it with the existing opportunity
revision check. This deliberately avoids collapsing multiple lots/times into a date-only field.

New safe errors include BKMS_INVALID_EFORMS (502), BKMS_EFORMS_IDENTITY_MISMATCH (502),
BKMS_DEADLINE_CONFLICT (409); existing quota/conflict/rate-limit/timeout/storage errors remain.
No response body, XML text, SQL, path or trace is echoed. Failed calls are not empty/successful loads.
A lost mutation response means metadata persistence is uncertain; inspect/read again before an
explicit retry. No request is automatically retried.

## Offline checks, browser workflow and performance

```powershell
$env:HF_HUB_OFFLINE = "1"
.\.venv\Scripts\python.exe -m pytest --cov=tendercite --cov-report=term-missing
.\.venv\Scripts\ruff.exe check .
.\.venv\Scripts\ruff.exe format --check .
npm --prefix web run typecheck
npm --prefix web run lint
npm --prefix web run format:check
npm --prefix web test
npm --prefix web run build
```

```sh
HF_HUB_OFFLINE=1 .venv/bin/pytest --cov=tendercite --cov-report=term-missing
.venv/bin/ruff check .
.venv/bin/ruff format --check .
npm --prefix web run typecheck && npm --prefix web run lint && npm --prefix web run format:check && npm --prefix web test && npm --prefix web run build
```

The [controlled multi-day browser setup](multi-day-german-procurement-discovery.md#controlled-browser-check)
now also serves original synthetic eForms ZIPs, never real providers. Use a **fresh disposable**
temporary database for each scenario. With that backend, Vite and installed Playwright Chromium:

```powershell
$env:E2E_BASE_URL = "http://127.0.0.1:5174"
$env:TENDERCITE_CONTROLLED_DISCOVERY_SMOKE = "1"
npm --prefix web run test:browser -- e2e/bkms-deadlines.spec.ts
```

```sh
E2E_BASE_URL=http://127.0.0.1:5174 TENDERCITE_CONTROLLED_DISCOVERY_SMOKE=1 npm --prefix web run test:browser -- e2e/bkms-deadlines.spec.ts
```

This covers explicit load, single/multi-lot/conflicting/missing/unsupported values, original links,
native import, a version-checked manual deadline edit and unchanged native readback after reload.
Desktop/mobile screenshots remain ignored. Existing TED, seven-day, matching, Pipeline and
PDF-to-capability-report scenarios run separately against fresh databases.

The reproducible offline benchmark uses temporary SQLite and generated normalized metadata, not
real notices, embeddings or downloads. Seven days × 1,000 records, 1,000-character descriptions,
exact duplicates/versions, one warmup + five measured runs per query, page 1/20 hits:

```powershell
.\.venv\Scripts\python.exe -m evaluation.bkms_search_benchmark
.\.venv\Scripts\python.exe -m evaluation.bkms_search_benchmark --with-enrichment
```

```sh
.venv/bin/python -m evaluation.bkms_search_benchmark
.venv/bin/python -m evaluation.bkms_search_benchmark --with-enrichment
```

On 2026-11-05, Python 3.12.14 / Pydantic 2.13.5 / SQLite 3.53.1, Linux, host-visible AMD EPYC 9V74
(5 logical CPUs reported), 13,207,257 active catalog bytes:

| Query | Matches | Baseline median / max (ms) | With enrichment median / max (ms) |
| --- | ---: | ---: | ---: |
| Software / all categories | 6,400 | 353.28 / 374.37 | 372.01 / 381.68 |
| Software / award | 2,134 | 236.24 / 312.96 | 255.91 / 283.99 |
| UnmatchedPhrase / all | 0 | 221.46 / 222.58 | 240.43 / 255.65 |

[Machine-readable measurements](../evaluation/german-discovery-search-performance.json) contain all
individual timings/environment/configuration. Timings include SQLite payload loading, validation,
local filtering/deduplication/sorting and selected-page enrichment, excluding HTTP/React rendering.
They are warm local observations, not SLAs or extrapolations to the 28,000-record maximum. They do
not justify adding a new search engine in this milestone. Larger retained corpora/lot counts still
need measurement. Only up to 20 selected evidence wrappers are constructed per result page.

Remaining limits: verified modern customization only; no full national-schema/legal validation,
legacy deadline adapter, request-to-participate/other deadline types, automatic adoption, inferred
timezones, open-bid determination, eForms document ingestion, attachment fetch or scheduled refresh.

Validation on 2026-11-05: **1,707 offline Python tests passed**, three opt-in live tests skipped,
96% coverage; **668 React tests passed** across 36 files. Ruff lint/format, TypeScript, ESLint,
Prettier and production build passed. **Ten Chromium workflows passed** with synthetic providers
and fresh real SQLite databases, including TED, multi-day German discovery, native matching/Pipeline
and PDF-to-capability reporting. Genuine desktop/mobile screenshots were inspected; 390-pixel
overflow checks passed. The existing frontend large-chunk warning remains (about 558 kB minified
JavaScript). No private pilot, real workforce records, paid provider, model download, annotation,
ledger or dependency-lock changes were involved.
