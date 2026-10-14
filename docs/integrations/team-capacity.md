# Local Team Capacity Engine snapshots (4.1b)

TenderCite consumes schema **1.0** from `nak224/team-capacity-engine` independently,
without installing/importing its Python classes. Producer PR #14 was merged into `work`
at `602f979c2932c4be9438e69e0a3e219518e86810`; its
[merged implementation CI](https://github.com/nak224/team-capacity-engine/actions/runs/38381900299)
passed. The byte-for-byte [synthetic example](../../tests/fixtures/capabilities/tendercite-capability-v1.json)
was pinned on 2026-10-14; [fixture provenance](../../tests/fixtures/capabilities/README.md)
records the original path, file checksum and canonical content hash.

The consumer has the same JSON fields, enum values, nullability, ordering, period bounds,
Decimal constraints and seven ordered source limitations. There are no intentional data-contract
differences. Its class names and error handling are independent. The additional input safeguards
reject duplicate keys, nonfinite JSON, unsafe files and files exceeding 16 MiB.
Future schema versions fail explicitly; they require a separate reviewed consumer contract.

## Validation and canonical identity

`tendercite.domain.capabilities_v1` implements immutable Pydantic v2 models. Unknown fields
are forbidden at every level. Members, member assignments and catalog entries must have unique
UUIDs in ascending UUID order. The catalog must contain exactly all referenced skills; empty
assignments/catalog/workforce are valid. Engagement types are `employee`, `freelancer` and
`external_partner`; skill source is `internal_skill_assignment`. Proficiency is nullable or a
strict integer from 1 to 5. Dates are `YYYY-MM-DD`, with 1–366 inclusive days.

Hours use Decimal, with the producer's maximum 24 digits and two decimal places. JSON numeric
hours, exponent strings, nonfinite values and negative nonnegative fields are rejected.
Producer-compatible redundant trailing zeroes are accepted and normalized, without rounding.
Signed zero is preserved. No additional arithmetic/qualification rules are imposed.

Canonical serialization uses the **validated** schema representation: canonical UUID strings,
ISO dates, exactly two decimal places for hours, UTF-8, sorted object keys, compact separators
`(',', ':')`, `ensure_ascii=False` and `allow_nan=False`. Array order is checked, never repaired.
The hash is `sha256:` followed by the lowercase hex SHA-256 of canonical JSON **excluding only
`content_hash`**. Canonical storage/export includes the verified hash field.

Raw file bytes can be pretty-printed or differently ordered without changing normalized identity.
They are read without modification and are not stored separately. Parsed/validated content,
canonical bytes and the verified content hash are distinct; import timestamps are local metadata
and do not participate in hashing. Hash verification detects changes relative to the supplied
identity, but anyone can recompute it. It proves neither origin/authentication nor workforce truth,
authorized export, independently verified skills or employment status. No signatures are added.

## Export and transfer synthetic records

In an initialized Team Capacity Engine development checkout, use synthetic seed records and its
existing PostgreSQL setup. The producer exporter is POSIX-only; Windows operators can run it in
the existing Linux Compose service. It does not initialize or migrate the source database.
See the [producer guide](https://github.com/nak224/team-capacity-engine/blob/602f979c2932c4be9438e69e0a3e219518e86810/docs/integrations/tendercite.md)
for setup, permissions, record bounds and safe replacement.

Linux, from the producer root:

```sh
uv run --no-sync python -m scripts.export_tendercite_snapshot --start-date 2027-02-01 --end-date 2027-02-07 --output .local/exports/tendercite/week.json --acknowledge-local-synthetic-data
mkdir -p ../tendercite/data/capabilities && chmod 700 ../tendercite/data/capabilities
cp -n .local/exports/tendercite/week.json ../tendercite/data/capabilities/week.json
```

PowerShell, from the producer root (each command is one line):

```powershell
$exportContainer = 'tce-capability-export-' + [guid]::NewGuid().ToString('N')
docker compose run --name $exportContainer api uv run --no-sync python -m scripts.export_tendercite_snapshot --start-date 2027-02-01 --end-date 2027-02-07 --output /tmp/tendercite/week.json --acknowledge-local-synthetic-data
if ($LASTEXITCODE -ne 0) { throw 'Synthetic export failed; do not copy its output' }
New-Item -ItemType Directory -Force ..\tendercite\data\capabilities | Out-Null
docker cp "${exportContainer}:/tmp/tendercite/week.json" "../tendercite/data/capabilities/$exportContainer.json"
docker rm $exportContainer
```

Use a restricted local directory or an approved secure transfer channel. Do not put snapshots
in shared links, tickets, chat, Git, provider prompts or telemetry. Windows directories/SQLite
files inherit ACLs; restrict them to the operator. Linux operators should use private directories
and `umask 077` before creating a database. Existing database permissions are not changed.
Keep custom `TENDERCITE_DATA_DIR` locations private too. Stable UUIDs are pseudonymous, not
anonymous; catalog labels can themselves contain sensitive free text despite the field allowlist.
`data/capabilities/`, SQLite data and Docker build data are ignored by default. An acknowledgement
is an operator declaration of synthetic data, not an automatic privacy/source audit.

## Validate and import the exact pinned example

Run from TenderCite's root with its existing Python 3.12 environment. Validation-only reads no
application configuration, initializes no database and needs no LLM credentials. For this demo,
choose an isolated ignored database directory; normal imports use the existing
`TENDERCITE_DATA_DIR`/`.env` setting and `tendercite.db`.

PowerShell:

```powershell
.\.venv\Scripts\python.exe -m tendercite.cli.import_capabilities --validate-only --input .\tests\fixtures\capabilities\tendercite-capability-v1.json
$env:TENDERCITE_DATA_DIR = '.\data\capabilities\demo-db'
.\.venv\Scripts\python.exe -m tendercite.cli.import_capabilities --input .\tests\fixtures\capabilities\tendercite-capability-v1.json --acknowledge-local-synthetic-data
.\.venv\Scripts\python.exe -m tendercite.cli.import_capabilities --list --limit 20
.\.venv\Scripts\python.exe -m tendercite.cli.import_capabilities --get sha256:e15ea7ea84176a4a9edf4bce710ede9008b620aa8329ddc14cb799354e9d6134
.\.venv\Scripts\python.exe -m tendercite.cli.import_capabilities --get sha256:e15ea7ea84176a4a9edf4bce710ede9008b620aa8329ddc14cb799354e9d6134 --output .\data\capabilities\retrieved-1.json
```

Linux:

```sh
.venv/bin/python -m tendercite.cli.import_capabilities --validate-only --input tests/fixtures/capabilities/tendercite-capability-v1.json
umask 077
export TENDERCITE_DATA_DIR='./data/capabilities/demo-db'
.venv/bin/python -m tendercite.cli.import_capabilities --input tests/fixtures/capabilities/tendercite-capability-v1.json --acknowledge-local-synthetic-data
.venv/bin/python -m tendercite.cli.import_capabilities --list --limit 20
.venv/bin/python -m tendercite.cli.import_capabilities --get sha256:e15ea7ea84176a4a9edf4bce710ede9008b620aa8329ddc14cb799354e9d6134
.venv/bin/python -m tendercite.cli.import_capabilities --get sha256:e15ea7ea84176a4a9edf4bce710ede9008b620aa8329ddc14cb799354e9d6134 --output data/capabilities/retrieved-1.json
```

The expected identity is `sha256:e15ea7ea84176a4a9edf4bce710ede9008b620aa8329ddc14cb799354e9d6134`.
The safe summary has 3 members, 3 assignments and 2 catalog entries, for 2027-02-01 through
2027-02-07. First import reports `INSERTED`; repeating it reports `ALREADY_PRESENT` with the
original timestamp. Validation reports `VALIDATED`; inspection reports `LISTED`/`FOUND`.
Full retrieval requires an explicit hash. `--get --output` writes complete canonical JSON to a
new private local file and reports `EXPORTED`; existing output paths are refused. Choose a fresh
name for each export. Complete snapshots/individual capacity values are never printed.

## File and storage guarantees

Only local `.json` files are accepted, up to 16 MiB; URLs, UNC/device paths, traversal components,
directories, nonregular files and symlinks are refused. The POSIX reader pins every directory
descriptor, uses `O_NOFOLLOW` and nonblocking opening, then validates the opened regular file.
The Windows reader uses native handles, rejects reparse points, redirected ancestors and mapped
remote drives, and denies sharing for writes/deletion during reading. Resolved path aliases may
be refused. Unsupported native protections fail closed. Reads are bounded, and size/mtime/ctime
changes during reading fail. JSON values are never executed or interpreted as filesystem paths.
The local OS/filesystem and operator remain trusted; this is not protection from a hostile kernel.

`SqliteRepository._initialize()` adds only `capability_snapshots` and its metadata index.
The primary key is the verified hash; other columns hold schema/source, period boundaries,
canonical JSON and UTC import time. `BEGIN IMMEDIATE` plus database-enforced uniqueness and
`ON CONFLICT(content_hash) DO NOTHING` handle concurrent duplicate imports. Validation precedes
the write; failures roll back. Existing content/identity is revalidated before idempotent success,
and complete lookups recheck canonical bytes/hash/metadata. Inconsistency fails without overwrite.

Different verified hashes for the same period are retained as distinct historical states. No
latest snapshot is automatically authoritative. Listings select metadata columns only, ordered
by import timestamp descending then hash ascending, with a strict 1–100 limit (default 50).
Read-only CLI inspection opens SQLite with `mode=ro`, without initialization or database creation.
The repository API exposes `import_capability_snapshot`, `get_capability_snapshot` and
`list_capability_snapshots`. Export is local file output, not an HTTP download endpoint; interrupted
exports can leave an incomplete private output file. Validate it or remove that known failed
output before choosing a fresh name. SQLite transaction durability follows existing filesystem
and SQLite behavior; no new database or migration framework is introduced.

CLI exit codes are 0 success, 2 rejected input/configuration/integrity/output, 3 not found, and
1 database/OS failure. Errors contain only fixed categories: `INVALID_ARGUMENTS`,
`ACKNOWLEDGEMENT_REQUIRED`, `UNSAFE_INPUT`, `UNSUPPORTED_PLATFORM`, `FILE_READ_FAILED`,
`INPUT_TOO_LARGE`, `INPUT_CHANGED`, `INVALID_JSON`, `UNSUPPORTED_SCHEMA`, `INVALID_CONTRACT`,
`HASH_MISMATCH`, `STORAGE_INTEGRITY`, `CONFIGURATION_FAILED`, `DATABASE_FAILED`, `NOT_FOUND`,
or `OUTPUT_FAILED`. No input paths, personnel values, SQL exceptions or credentials are echoed.

## Meaning and remaining scope

Known zero capacity stays distinct from unknown. Unknown assessed fields remain null while
recorded unavailable/allocated hours remain visible. Negative remaining balances and daily
overallocation are preserved; a positive period balance does not erase daily conflicts. Totals
are shared, period-specific, unreserved and not contractual availability or intraday feasibility.
Recurring source schedules have no historical versions; stored states do not prove past accuracy.

Skills/proficiency are internal assignments, not years of experience, certificates, references,
verified competence or tender eligibility. The producer exports active members; TenderCite does
not independently verify employment/active status. Import creates no tender association,
assessment, requirement confirmation, team selection, allocation or Go/No-Go update.

Tests use only the exact pinned synthetic fixture and derived synthetic cases. They cover schema,
hashing, file safety, privacy, concurrent imports, corruption, restarts, actual CLI subprocesses
and existing tender/analysis/review/decision preservation. Linux file handling is exercised;
Windows native handle checks are mocked. Native Windows filesystem/ACL verification and optional
fresh producer exports require the corresponding local environment. CI requires neither the
producer repository nor PostgreSQL, model downloads or network access for these tests.

Local validation on 2026-10-14: **894 offline tests passed**, with one optional live TED test
skipped; the 183 new integration tests all passed. Ruff lint/format passed. A coverage-enabled
local run reached the existing UI test's three-second startup timeout; its assertions pass in
the standard offline suite. Production UI and test timeouts are unchanged. The Linux Docker
image built successfully with the pinned dependencies and verified proxy CA trust.

The next matching milestone will need separate verified experience/qualification evidence and
explicit human decisions. Matching, scoring, rankings, reservations, synchronization, UI and
production authentication are outside this importer milestone.
