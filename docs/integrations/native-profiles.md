# Native manual capability profiles (UI-1b.7)

**Profiles** at `/profiles` stores manually declared freelancer or company capabilities in
TenderCite itself. Create, reopen and edit profiles entirely in React: no imported file, CLI
operation, Team Capacity Engine instance, workforce snapshot, AI model or external API is needed.

## Local setup and manual workflow

Use the existing [standalone workspace setup](../web-workspace.md). The separate local-development
flag is disabled by default. Point to a disposable directory for synthetic testing, then restart
the backend after enabling it. PowerShell:

```powershell
$env:TENDERCITE_DATA_DIR = "data/curated/native-profile-demo"
$env:TENDERCITE_RETRIEVAL_ENABLED = "false"
$env:TENDERCITE_PROFILES_API_ENABLED = "true"
.\.venv\Scripts\python.exe -m uvicorn tendercite.main:app --host 127.0.0.1 --port 8001
```

Linux:

```sh
TENDERCITE_DATA_DIR=data/curated/native-profile-demo TENDERCITE_RETRIEVAL_ENABLED=false TENDERCITE_PROFILES_API_ENABLED=true .venv/bin/python -m uvicorn tendercite.main:app --host 127.0.0.1 --port 8001
```

Run Vite with `TENDERCITE_PROXY_TARGET=http://127.0.0.1:8001` as documented in the workspace
guide. The existing fixed local proxy is unchanged. No snapshot/mapping/report flags are needed.
Clear provider configuration for synthetic testing. Keep backend/frontend on loopback; the flag
is **not authentication**, accounts, permissions, multi-tenancy or public-deployment readiness.

1. Open **Profiles**, then **Create profile**.
2. Enter a name; select Freelancer or Company. Headline, location and work arrangement are optional.
3. Choose **Add skill** for each declaration. Enter names such as Python, Machine Learning,
   Computer Vision, React and Project Management. Edit names or remove rows directly. Internal
   skill UUIDs are generated automatically and retained through edits; users never enter IDs.
4. Optionally enter opportunity-interest keywords, one per line. These preferences do not trigger
   matching or recommendations.
5. Choose **Save profile** and wait for server confirmation. Reopen a saved profile from the list,
   edit it and save explicitly again. Reloading the browser reads persisted SQLite records.

The list pages through 50 metadata records at a time. Multiple profiles are independent; names
need not be globally unique. No autosave occurs. Unsaved drafts live only in page memory and
are lost on navigation/reload. The entire profile is editable; no skill is verified by saving it.

## Independent versioned contracts

| Contract | Version | Contents |
| --- | --- | --- |
| Native profile | `native-capability-profile-1` | Name/type, optional headline/location/arrangement, skills, preference keywords, manual origin and unverified qualification |
| Create | `native-profile-create-1` | Stable client-generated profile UUID and complete profile |
| Update | `native-profile-update-1` | Expected current revision and complete replacement profile |
| Stored record | `native-profile-record-1` | Profile UUID, revision, creation/update timestamps and profile |
| Save result | `native-profile-save-result-1` | CREATED, ALREADY_PRESENT or UPDATED plus authoritative record |
| Listing | `native-profile-listing-1` | Bounded name/type/identity/revision/timestamp metadata and next_offset |

Profile type is FREELANCER or COMPANY. Work arrangement is REMOTE, HYBRID, ON_SITE or null.
Names are 1–120 characters, headline at most 300, location at most 120. Up to 100 skills each have
an internal UUID, a 1–100-character name, `source_origin=manual_operator_entry` and
`verification_status=NOT_VERIFIED`. Up to 20 keyword preferences have 1–80 characters each.
Empty skill/preference lists are valid. Strings are trimmed. Duplicate skill IDs and normalized
names are rejected; the server compares Unicode NFKC, case-folded and whitespace-collapsed
labels. Preferences use the same duplicate check. Browser checks are convenience checks;
server validation remains authoritative. Strict models reject unexpected fields and versions.

These are **native profiles**, not schema 1.0 workforce snapshots. There are no workforce members,
capacity periods, external catalog requirements or fabricated `team-capacity-engine` provenance.
Internal skill IDs do not imply equivalence to an imported snapshot's catalog UUIDs. Existing
snapshot imports, mappings and matching remain separate and unchanged. No adapter or profile
matching is implemented. Stable identities and explicit profile/skill provenance provide a basis
for later extraction, but any future extraction source must be introduced explicitly with honest
provenance/versioning; the current API accepts only manual, unverified declarations.

## HTTP contract

All endpoints are under `/api/v1/profiles`, independently gated by
`TENDERCITE_PROFILES_API_ENABLED=false` by default. Responses and handled errors use
`Cache-Control: no-store`.

| Method | Path | Request | Response |
| --- | --- | --- | --- |
| POST | `/profiles` | ProfileCreate | 201 CREATED, or 200 ALREADY_PRESENT for identical creation |
| GET | `/profiles?limit=50&offset=0` | limit 1..100; offset 0..1,000,000 | ProfileListing in stable UUID order |
| GET | `/profiles/{profile_id}` | UUID | ProfileRecord |
| PUT | `/profiles/{profile_id}` | ProfileUpdate | 200 UPDATED; revision must still match |

Synthetic creation example (the UI supplies UUIDs automatically):

```json
{
  "schema_version": "native-profile-create-1",
  "profile_id": "20000000-0000-0000-0000-000000000001",
  "profile": {
    "schema_version": "native-capability-profile-1",
    "name": "Synthetic freelancer",
    "profile_type": "FREELANCER",
    "headline": "Synthetic software services",
    "location": "",
    "work_arrangement": "REMOTE",
    "skills": [{
      "skill_id": "20000000-0000-0000-0000-000000000002",
      "name": "Python",
      "source_origin": "manual_operator_entry",
      "verification_status": "NOT_VERIFIED"
    }],
    "opportunity_keywords": ["Software Development"],
    "source_origin": "manual_operator_entry",
    "qualification": "NOT_VERIFIED"
  }
}
```

An update sends `{"schema_version":"native-profile-update-1","expected_revision":1,"profile":...}`
with the entire profile. No paths, URLs, CLI arguments or database selectors are accepted.
POST/PUT JSON bodies are limited to 65,536 bytes, including streamed bodies without Content-Length.
The existing bounded capability transport rejects duplicate JSON keys, non-finite constants,
invalid/ambiguous length headers, unsupported content types/encodings and unknown query selectors.
Errors use only `{"detail":{"code":"..."}}`, never profile contents, raw field names, SQL,
database paths or traces. Codes include PROFILES_API_DISABLED (404), INVALID_PROFILE_INPUT (422),
PROFILE_NOT_FOUND (404), PROFILE_ID_CONFLICT/PROFILE_REVISION_CONFLICT/PROFILE_STORAGE_INTEGRITY
(409), PROFILE_REQUEST_TOO_LARGE (413), DATABASE_FAILED (503) and PROFILE_OPERATION_FAILED (500).

## Persistence, conflicts and uncertain outcomes

One additive `native_profiles` SQLite table stores UUID, revision and bounded versioned payload.
Normal repository initialization creates it if missing, preserving existing tables and records.
Each save uses BEGIN IMMEDIATE; updates check expected_revision inside the same transaction and
in the SQL update predicate. Competing edits produce a conflict instead of last-writer-wins.
This is current-record versioning, **not an immutable profile revision-history feature**.

The browser holds one creation ID for a draft. Repeating that exact creation returns the original
revision-1 record without rewriting it; changed content or an already edited record with that ID
returns a conflict. No existing profile is silently replaced. Updates increment the revision;
they are not automatically retried or rebased. Invalid storage records fail safely.

The UI prevents overlapping saves and retains form values after failures. A lost/invalid response
does not prove nothing was stored. It reads current server state separately without replacing
your edits or claiming that the failed save was confirmed. **Check current saved version** retries
only the read. **Load current saved version (discard my edits)** explicitly replaces the form and
its expected revision. Reconcile first, then make an explicit new save. Do not leave the page
before confirmation if you need to retain unsaved form values.

## Privacy, tests and remaining work

Profiles and skills are user declarations, not independently verified qualifications, certificates,
experience, bidder eligibility, available capacity or Go/No-Go judgments. Storage is local SQLite,
not encrypted storage or access control. No profile content is sent to providers, external APIs,
browser storage or application logs. Full loaded profiles remain in component memory; list queries
retain only bounded metadata with no retained cache after unmount. Use synthetic records in tests
and keep permitted local data private. The private pilot database is not used by this milestone.

Offline tests cover contracts, duplicates, CRUD, concurrency, restart persistence, storage integrity,
safe HTTP transport/errors and preservation of other tables. Frontend tests cover freelancer/company
forms, skill edits, conflicts and uncertain saves without retries. The opt-in generated-data browser
test covers five skills → save → reload → edit/remove/add → save, independently of a snapshot.
The existing tender browser workflow also remains supported; see [workspace tests](../web-workspace.md).

CV/document extraction, enrichment, skill suggestions, semantic/keyword opportunity matching,
recommendations, ranking, profile-to-snapshot adapters, history export and profile deletion are not
implemented. Existing workforce snapshot matching is still a separate workflow. No next milestone
or qualification inference is introduced here.
