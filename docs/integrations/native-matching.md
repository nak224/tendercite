# Basic native profile–opportunity matching (UI-1b.9)

**Opportunity Matching** at `/matching` compares one saved freelancer/company profile with one
saved opportunity. Create both through `/profiles` and `/opportunities`, select them, then click
**Compare Skills**. Selection and page loading never run a comparison automatically. Selectors
page through saved metadata in groups of 50, displaying names, types and opportunity organizations.
Only stored records participate; unsaved form edits elsewhere are not compared.

## Exact comparison and interpretation

For each `NativeOpportunity.required_skills` string, compare against
`NativeCapabilityProfile.skills[].name` using, in order:

1. Unicode NFKC normalization.
2. Python Unicode `casefold()`.
3. Collapse consecutive whitespace to one space and trim it.
4. Exact equality of the resulting labels.

`Python`/`python`, `Machine  Learning`/`machine learning`, `FastAPI`/`fastapi`, full-width Latin
letters and `Straße`/`STRASSE` match. `Java`/`JavaScript`, `ML`/`Machine Learning`, `React`/`React Native`
and accented/unaccented words do not. No substring search, synonym expansion, fuzzy comparison,
embedding or AI runs. Descriptions, preferences, keywords, deadlines, source URLs, locations and
work arrangements are ignored. URLs are never fetched.

The result preserves saved original labels alongside normalized labels, profile/opportunity
identity and revision, and exact counts. Each opportunity label is `MATCHED` or `NOT_RECORDED`.
`NOT_RECORDED` means no equal declaration was found; it does **not** establish an absent skill.
Empty profile skills produce nonmatches. Empty opportunity skills produce `NO_REQUIREMENTS`
and zero counts, never a perfect match. There is no score, percentage, ranking or suitability verdict.

Both sides are manual declarations. A matching label proves no experience, qualifications,
certification, availability, eligibility, tender fulfillment or legal compliance. Qualification
and eligibility remain `NOT_VERIFIED`; availability remains `NOT_EVALUATED`.

Use [Pipeline](native-pipeline.md) to track pursuing a saved opportunity explicitly. Matching is
optional, never creates a pipeline entry and never changes its stage or infers an outcome.

This is independent of **Capability Matching**, which uses imported workforce snapshots, reviewed
findings, explicit approved ALL_OF UUID mappings and recorded individual skill assignments. Native
profile skill UUIDs identify declarations only. No snapshot is fabricated and neither system
changes the other, review history, manual assessments or Go/No-Go state.

## Local configuration

Start from the [workspace setup](../web-workspace.md) with a permitted synthetic local database.
All three flags below are separate and **false by default**. The matching flag enables only the
read-only preview; it does not enable CRUD. Flags provide no authentication or public hosting safety.

PowerShell, backend terminal in the TenderCite repository:

```powershell
$env:TENDERCITE_DATA_DIR = "data/curated/native-matching-synthetic"
$env:TENDERCITE_RETRIEVAL_ENABLED = "false"
$env:TENDERCITE_PROFILES_API_ENABLED = "true"
$env:TENDERCITE_OPPORTUNITIES_API_ENABLED = "true"
$env:TENDERCITE_NATIVE_MATCHING_API_ENABLED = "true"
.\.venv\Scripts\python.exe -m uvicorn tendercite.main:app --host 127.0.0.1 --port 8001
```

Linux equivalent:

```sh
TENDERCITE_DATA_DIR=data/curated/native-matching-synthetic TENDERCITE_RETRIEVAL_ENABLED=false TENDERCITE_PROFILES_API_ENABLED=true TENDERCITE_OPPORTUNITIES_API_ENABLED=true TENDERCITE_NATIVE_MATCHING_API_ENABLED=true .venv/bin/python -m uvicorn tendercite.main:app --host 127.0.0.1 --port 8001
```

Frontend terminal:

```powershell
$env:TENDERCITE_PROXY_TARGET = "http://127.0.0.1:8001"
npm --prefix web run dev
```

```sh
TENDERCITE_PROXY_TARGET=http://127.0.0.1:8001 npm --prefix web run dev
```

Open `http://127.0.0.1:5174/matching`. Only the existing fixed same-origin development proxy is
used. Requests omit credentials, reject redirects and use `no-store`; POSTs are never retried.
Matching results stay in component memory, outside browser storage and the query cache. Changing
either selection, paging, refreshing or leaving the page discards results and aborts/ignores
outdated requests. A production build has no configured development integration.

## HTTP contract and consistency

`POST /api/v1/native-matches/preview` accepts only this versioned saved-record selection:

```json
{
  "schema_version": "native-match-selection-1",
  "profile_id": "20000000-0000-0000-0000-000000000001",
  "opportunity_id": "30000000-0000-0000-0000-000000000001",
  "expected_profile_revision": 1,
  "expected_opportunity_revision": 1
}
```

Use actual UUIDs from the synthetic saved records; illustrative IDs above do not seed records.
Each revision is an integer in 1–2,147,483,647. Arbitrary contents, paths, URLs and extra fields
are rejected. The existing bounded transport enforces 65,536 bytes even without Content-Length,
strict JSON (duplicate keys and non-finite values rejected), content type and encoding rules.

The repository opens existing state read-only (`mode=ro`, no initialization) and reads both records
in **one SQLite BEGIN read transaction**, validating their storage integrity and expected revisions
inside that snapshot. It creates no table and writes nothing. A writer can commit a later version
after the snapshot starts; the result honestly identifies the coherent versions it compared, not
a promise that those versions remain current afterward.

`native-match-result-1` includes `algorithm_version: nfkc-casefold-whitespace-equality-1`, status,
profile metadata and `skills[{skill_id,original_label,normalized_label}]`, opportunity metadata and
`required_skills[{original_label,normalized_label,status,matched_profile_skill_ids}]`,
`counts{total_opportunity_skills,matched_skills,not_recorded_skills}` and evidence limitations.
The React client checks schema/algorithm, selected identities/revisions, metadata, bounds, unique
labels/IDs, referenced skill membership, status/count consistency and evidence boundaries. Server
casefolded labels are authoritative; the browser does not substitute JavaScript lowercasing.

All responses, including fixed errors, use `Cache-Control: no-store`. Codes:

| HTTP | Fixed code |
| --- | --- |
| 404 | `NATIVE_MATCHING_API_DISABLED`, `PROFILE_NOT_FOUND`, `OPPORTUNITY_NOT_FOUND` |
| 409 | `PROFILE_REVISION_CONFLICT`, `OPPORTUNITY_REVISION_CONFLICT`, `PROFILE_STORAGE_INTEGRITY`, `OPPORTUNITY_STORAGE_INTEGRITY` |
| 422 | `INVALID_NATIVE_MATCH_INPUT` |
| 413 | `NATIVE_MATCH_REQUEST_TOO_LARGE` |
| 503 | `DATABASE_FAILED` |
| 500 | `NATIVE_MATCH_FAILED` |

Existing fixed transport codes cover unsupported media/encoding and invalid/incomplete lengths.
No raw input, database path, SQL or exception text is returned. After an error the UI requires
**Refresh saved records** and reselection. It neither silently adopts changed revisions nor retries
the comparison. An identical-content save still increments the revision and requires refresh.

## Synthetic verification

Offline checks:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_native_matching.py tests/test_native_matches_api.py
npm --prefix web test -- src/api/nativeMatching.test.ts src/pages/OpportunityMatching.test.tsx
```

The opt-in Chromium test needs **its own fresh empty disposable directory**, not the database used
by the three existing tender/profile/opportunity browser tests. It refuses nonempty profile and
opportunity lists before writing. Replace the data-directory assignment in the backend setup above
with this fresh directory, keeping the other flags and server command:

```powershell
$env:TENDERCITE_DATA_DIR = Join-Path ([System.IO.Path]::GetTempPath()) ("tendercite-native-match-" + [guid]::NewGuid().ToString())
```

```sh
export TENDERCITE_DATA_DIR="$(mktemp -d /tmp/tendercite-native-match-XXXXXX)"
```

Keep that explicit directory while running the two servers, then terminal 3:

```powershell
$env:E2E_BASE_URL = "http://127.0.0.1:5174"
$env:TENDERCITE_NATIVE_MATCHING_SMOKE = "1"
npm --prefix web run test:browser -- e2e/native-matching.spec.ts
```

```sh
E2E_BASE_URL=http://127.0.0.1:5174 TENDERCITE_NATIVE_MATCHING_SMOKE=1 npm --prefix web run test:browser -- e2e/native-matching.spec.ts
```

Use installed Chromium via `PLAYWRIGHT_CHROMIUM_EXECUTABLE`, or install Playwright Chromium as
documented in the workspace guide. This test explicitly creates skills/requirements in React,
compares 2 of 4 labels, edits the profile in a second tab, observes a revision conflict, explicitly
refreshes, then compares 3 of 4. Synthetic declarations and screenshots establish workflow behavior,
not genuine professional evidence. No real procurement or workforce data is needed.

Later improvements such as semantic matching, extraction, recommendations, discovery, ranking and
result persistence are separate work. None is implemented or implied by this milestone.

Validation on 2026-10-30 (Python 3.12.14, Node 24.19.0): full offline Python suite **1,514 passed,
1 live-test skip**, Ruff lint/format passed; React **493 tests across 24 files passed**,
TypeScript/ESLint/Prettier/build passed. Chromium passed the new native matching flow (including
stale revision/refresh) against an isolated synthetic database. The three existing opportunity,
profile and PDF-to-capability-report smokes passed against a separate fresh database. A genuine
desktop screenshot was inspected; comparison spacing and original-label whitespace were checked.
No private pilot, real workforce data, provider call or model download was used.
