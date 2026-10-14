# Persistent requirement-to-skill mappings (4.2b)

This local workflow records an operator's explicit association between a reviewed finding and
catalog skill UUIDs. It extends [candidate discovery](capability-matching.md) without changing its
matching algorithm, production API/UI, retrieval or paid evaluation workflows. It does not prove
that a person, team or bidder fulfills a tender requirement.

## Contracts and identity

`domain/capability_mapping_records.py` defines independent, strict, frozen Pydantic contracts.
Unexpected fields and unsupported versions/operators are rejected. JSON mode permits UUID and
UTC timestamp strings while preserving strict integers, enums and other field types.

| Contract | Version |
| --- | --- |
| Persisted revision | `capability-mapping-record-1` |
| Draft input | `capability-mapping-draft-1` |
| Revision input | `capability-mapping-revision-1` |
| Approval input | `capability-mapping-approval-1` |
| Revocation input | `capability-mapping-revocation-1` |
| Current inspection | `capability-mapping-inspection-1` |
| Reused matching contract | `requirement-skill-mapping-1` |

Each mapping has a UUID and exactly one `(analysis_id, finding_id, snapshot_hash)` identity. The
database permits only one history for that combination. Different snapshots require distinct
mappings, including snapshots with identical periods/catalog UUIDs. There is no latest-snapshot
fallback. Skills must come from the imported, hash-validated catalog; selection is explicit
`ALL_OF`, with 1–100 unique UUIDs stored in canonical UUID order. Labels are never resolved
automatically. The producer's v1 catalog contains assigned skills only.

Each revision records its number, stored lifecycle status, original creation timestamp, revision
timestamp, approval timestamp where applicable, operator rationale (1–2,000 characters), optional
non-personal alias (1–64 ASCII letters/digits/underscores/hyphens), and matching contract version.
Use aliases rather than names or emails; syntax validation cannot establish that an alias is
non-personal or that the operator is authorized.

The review binding contains the latest `ReviewEvent.id` (nullable only for an unreviewed draft),
effective statement/category/type and their `sha256:` digest. The digest is SHA-256 over UTF-8
JSON with sorted keys, compact separators and unescaped Unicode. Text is not normalized. Finding
and analysis IDs are preserved separately in the revision; timestamps do not determine which
review is current. Existing review sequence order does.

## Lifecycle, review changes and transactions

| Operation | New stored revision | Matching use |
| --- | --- | --- |
| Create | DRAFT, revision 1 | Excluded |
| Approve current draft | APPROVED, next revision | Usable only while binding remains current |
| Revise any current revision | DRAFT, next revision | Excluded until a separate approval |
| Revoke current draft/approval | REVOKED, next revision | Excluded |
| Subsequent requirement review | No mapping write; derived STALE | Excluded |

Draft creation and revision check the caller's expected current review event and digest. Approval
also requires the draft's existing binding to equal that current binding, a CONFIRMED/MODIFIED
finding, VERIFIED_QUOTE grounding, correct analysis association and known catalog UUIDs. A later
MODIFY, CONFIRM or REJECT event invalidates the old approval **even if the text is identical**.
Changed effective values or lost eligibility also prevent use. STALE is a derived inspection
status; the historical APPROVED record remains intact. A stale approval cannot be approved
directly: inspect the new requirement, explicitly revise against its new binding, then approve
that draft. Revoked mappings also require an explicit new draft and separate approval to restore
use. A new draft or revocation never falls back to an older approval.

All mutations require `expected_revision`, except initial creation. A competing update fails with
`MAPPING_REVISION_CONFLICT`; a mismatched review fails with `REQUIREMENT_REVIEW_CONFLICT`. Reload
and reconsider the current state; do not blindly repeat with a higher revision.

`SqliteRepository` adds two tables: `capability_mappings` for immutable identity and
`capability_mapping_revisions` for append-only payloads. Primary/unique keys enforce identity and
revision uniqueness. Foreign keys reference existing analyses, findings, snapshots and review
sequence numbers. Triggers enforce finding/review association, consecutive revisions and prevent
UPDATE/DELETE of identities/history. No existing table is rebuilt or imported snapshot modified.
Initial identity and revision insertion is atomic. Mutations use `BEGIN IMMEDIATE`; mapping revision,
catalog integrity and latest requirement review are checked on the same connection before insert.
Concurrent requirement reviews therefore serialize with approval. Old read-only databases can
report no mappings without a migration; writable initialization installs the additive tables.

Matching loads all selected findings, current mappings and one explicit snapshot inside one
`BEGIN` read transaction. The result describes that consistent read snapshot; it cannot guarantee
that another operator will not review a finding after the read completes. There is no write,
automatic status update, HTTP request, LLM call or model initialization. A thin adapter builds the
existing `CapabilityMatchingRequest` and invokes the unchanged pure 4.2a algorithm. The Python
service returns `capability-matching-result-1`. Unavailable findings remain NOT_EVALUATED with:

- `NO_EXPLICIT_SKILL_MAPPING` for every unavailable mapping;
- `PERSISTED_MAPPING_NOT_APPROVED`, `PERSISTED_MAPPING_REVOKED` or `PERSISTED_MAPPING_STALE`
  to distinguish the existing lifecycle from a finding that never had a mapping.

## Local CLI and synthetic end-to-end example

Run from the repository root with the existing development environment installed. No provider
credentials, downloads or services are needed. For a disposable, complete offline demonstration:

Linux:

```sh
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 .venv/bin/python examples/persistent_capability_mappings.py
```

PowerShell:

```powershell
$env:HF_HUB_OFFLINE='1'; $env:TRANSFORMERS_OFFLINE='1'; .\.venv\Scripts\python.exe examples\persistent_capability_mappings.py
```

The example imports the unchanged synthetic Team Capacity Engine fixture and simulates a reviewed
PostgreSQL requirement. The manually selected skill is
`00000000-0000-0000-0000-000000000065`. Before approval, coverage is NOT_EVALUATED. After explicit
approval, two individuals have all required skills recorded and one has no required assignment.
The example then simulates a modification requiring five years of experience: the mapping becomes
STALE and matching returns NOT_EVALUATED. Original text, quotations and all revisions remain intact;
no experience duration or genuine human review is claimed.

For inspectable artifacts and subsequent explicit actions, use a **fresh** directory under ignored
`data/evaluation/`. The script refuses to overwrite an existing demo database. These commands each
occupy one line and use the generated versioned JSON files:

Linux:

```sh
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 .venv/bin/python examples/persistent_capability_mappings.py --directory data/evaluation/mappings-demo
.venv/bin/python -m tendercite.cli.capability_mappings --database data/evaluation/mappings-demo/synthetic.db list --analysis-id synthetic-analysis
.venv/bin/python -m tendercite.cli.capability_mappings --database data/evaluation/mappings-demo/synthetic.db revise --input data/evaluation/mappings-demo/3-revision.json
.venv/bin/python -m tendercite.cli.capability_mappings --database data/evaluation/mappings-demo/synthetic.db approve --input data/evaluation/mappings-demo/4-reapproval.json
.venv/bin/python -m tendercite.cli.capability_mappings --database data/evaluation/mappings-demo/synthetic.db match --input data/evaluation/mappings-demo/2-match.json
.venv/bin/python -m tendercite.cli.capability_mappings --database data/evaluation/mappings-demo/synthetic.db revoke --input data/evaluation/mappings-demo/5-revocation.json
```

PowerShell:

```powershell
$env:HF_HUB_OFFLINE='1'; $env:TRANSFORMERS_OFFLINE='1'; .\.venv\Scripts\python.exe examples\persistent_capability_mappings.py --directory data/evaluation/mappings-demo
.\.venv\Scripts\python.exe -m tendercite.cli.capability_mappings --database data/evaluation/mappings-demo/synthetic.db list --analysis-id synthetic-analysis
.\.venv\Scripts\python.exe -m tendercite.cli.capability_mappings --database data/evaluation/mappings-demo/synthetic.db revise --input data/evaluation/mappings-demo/3-revision.json
.\.venv\Scripts\python.exe -m tendercite.cli.capability_mappings --database data/evaluation/mappings-demo/synthetic.db approve --input data/evaluation/mappings-demo/4-reapproval.json
.\.venv\Scripts\python.exe -m tendercite.cli.capability_mappings --database data/evaluation/mappings-demo/synthetic.db match --input data/evaluation/mappings-demo/2-match.json
.\.venv\Scripts\python.exe -m tendercite.cli.capability_mappings --database data/evaluation/mappings-demo/synthetic.db revoke --input data/evaluation/mappings-demo/5-revocation.json
```

Use the UUID returned by the example or `list` with `inspect --mapping-id UUID` and
`history --mapping-id UUID`. History includes every stored revision in ascending revision order;
inspect reports derived current status. Lists are limited to 1–100 mappings (default 50), sorted by
analysis, finding and snapshot; use explicit filters. They are bounded selections, not a claim
that every mapping is shown in a large database.

For genuine local records, `review-state --analysis-id ID --finding-id ID --snapshot-hash HASH`
provides the current review binding. Create a `capability-mapping-draft-1` JSON file with those
identity fields, `operator`, `skill_ids`, `expected_review_event_id`, `expected_review_digest`,
`rationale` and optional `operator_alias`; run `create --input FILE.json`. Approval and revocation
are separate JSON operations with their respective schema versions, `mapping_id`,
`expected_revision`, rationale and optional alias. Approval also supplies the expected review event
and digest. Revision includes the explicit operator/UUID selection and fresh review binding.
The matching input is `capability-matching-request-1` with analysis ID, one snapshot hash, selected
finding IDs and an empty `mappings` array. Inline mappings are rejected in this CLI.

`--database` must precede the subcommand; it selects an existing local `.db`. Without it, only
`TENDERCITE_DATA_DIR` is read (default `data/tendercite.db`), independently of LLM configuration.
JSON input is limited to 64 KiB; existing local-only regular-file, symlink/race and duplicate-key
safeguards are reused. Errors use fixed categories without echoing paths, payloads or provider
configuration. Stdout contains mapping/review metadata; matching output aggregates coverage and
never prints personnel UUIDs, individual assignment records or capacity values. The Python service
retains the full 4.2a provenance result. There are no automatic retries or lifecycle transitions.

## Evidence boundaries and remaining scope

Approval is an operator declaration about an association. Neither source quotation occurrence nor
approval verifies semantic correctness, qualification, years of experience, certificates,
references, bidder eligibility, contractual availability or award-criterion fulfillment.
Proficiency remains unrelated to experience duration. Qualification stays NOT_VERIFIED and capacity
NOT_EVALUATED. Individual skills are not combined into team fulfillment, ranked or reserved. No
assessment, requirement review or Go/No-Go decision is changed by mapping operations.

Keep imported personnel snapshots, local JSON inputs and databases private and outside Git.
`data/evaluation/`, `data/capabilities/` and standard `data/*.db` paths are already ignored; custom
locations need equivalent exclusion. The synthetic committed fixture/example contains no genuine
personnel. Append-only SQL guards are audit safeguards, not authentication, signatures or protection
against an administrator replacing the database/dropping triggers. Operator aliases are declarations.
There is no multi-user authorization, export synchronization or history purge workflow.

4.2c can consume these versioned histories and explicit approval/review bindings for a separately
scoped workflow. Stronger qualification or capacity conclusions require independent evidence and
new explicit rules; no such inference is part of this milestone.
