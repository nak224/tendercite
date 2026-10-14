# Local capability assessment workflow (4.2c)

This local, offline workflow supports an operator assessing **existing persisted analysis
findings** against one explicitly selected imported workforce snapshot. It produces assessment
support, never a qualification or Go/No-Go decision. No provider configuration, HTTP requests,
embeddings, new database tables or matching algorithms are required.

## Architecture and contracts

`tendercite.services.capability_workflow` composes existing SQLite reads, review bindings,
mapping inspections and the persistent matching adapter. Its shared loader reads the analysis,
latest reviews, current mapping revisions and the verified snapshot in one `BEGIN` transaction.
The existing `match_persisted_capabilities` entry point and the workflow's inspection adapter
share the same mapping filtering and unchanged `explicit-uuid-membership-1` algorithm. There
is no second skill matcher or approval lifecycle. Overview, preparation and reporting use a
read-only repository; they do not initialize a database or change reviews, assessments,
Go/No-Go, snapshots or mapping history.

Strict, frozen Pydantic contracts forbid unknown fields and unsupported versions:

| Contract | Version | Purpose |
| --- | --- | --- |
| Analysis inventory | `capability-workflow-analysis-inventory-1` | Bounded IDs, timestamps and finding counts |
| Overview | `capability-workflow-overview-1` | Reviewed findings, catalog and mapping states |
| Operator mapping selection | `capability-workflow-mapping-selection-1` | Explicit ALL_OF UUIDs, rationale and optional alias |
| Assessment selection | `capability-workflow-assessment-selection-1` | Explicit analysis, full snapshot hash and finding IDs |
| Assessment report | `capability-workflow-report-1` | Provenance, states, recorded coverage and limitations |

Preparation exports the **existing** `capability-mapping-draft-1` contract, including the latest
review event ID and canonical effective-value digest. It does not persist or approve it. Existing
4.2b creation rejects it if the review changes before persistence, even for an identical later
CONFIRM. Existing mappings must be explicitly revised rather than recreated. Approval, revision
and revocation still require the expected current mapping revision and the existing transactional
checks. See [persistent mappings](persistent-capability-mappings.md) for their full contracts.

## Select an analysis and snapshot

Run commands from the repository root using the prepared Python environment. `--database` must
precede the command and identify an **existing** local `.db`. Without it, the workflow reads
`TENDERCITE_DATA_DIR/tendercite.db` (default `data/tendercite.db`) using local-only settings.
No unrelated database is initialized. The analysis inventory is ordered by ID and bounded to
1–100 entries; its default limit is 50. It contains no queries or requirement text.

```sh
python -m tendercite.cli.capability_workflow --database data/tendercite.db analyses --limit 50
python -m tendercite.cli.import_capabilities --list --limit 50
```

The existing snapshot listing uses `TENDERCITE_DATA_DIR`; choose the **full** verified
`sha256:…` content hash, not a date, file name, latest-import guess or prefix. For a custom
database, point that environment variable at its data directory when listing snapshots.
Snapshots remain separate even when they share catalog UUIDs.

Choose the analysis ID from this inventory or the previously returned `AnalysisRun.id`.
Requirements emitted by external QA, staged cloud LLM or legal-reference evaluation pipelines
are **not automatically persisted AnalysisRun findings**. No bridge from those evaluation
formats is introduced here. The separate [manual intake](curated-tender-intake.md) accepts explicit
candidates against normal ingested document IDs and starts them UNREVIEWED. Do not copy evaluation
IDs or treat their simulated/human review formats as interchangeable with finding reviews.

## Private local files

Console output contains counts, IDs, hashes, versions and statuses only. Full reviewed statements,
source quotes and catalog labels require an explicitly named private export. Reports also contain
pseudonymous person UUIDs and their matched/missing skill UUIDs; keep them private. They contain
no names, emails, raw capacity records or unrelated individual assignments.

Inside this checkout, workflow inputs/outputs are permitted under `data/capabilities/workflow/`
or `data/curated/` for the manual-intake handoff, both Git-ignored and Docker-excluded.
Explicit local paths outside checkouts/build contexts are also supported. Other Git checkouts
and detected Docker build directories are rejected. These guards do not discover every possible
custom build context: do not use a private directory as a Docker context or force-add its files.

Create parent directories yourself. Inputs are limited to 64 KiB, reject duplicate keys and
nonfinite JSON, and use the existing guarded regular-file reads. Symlinks/reparse points, remote
shares, URLs and traversal paths are rejected. Outputs are limited to 16 MiB and must be new files.
POSIX exports use 0600 temporary files, fsync and directory-FD-anchored exclusive hard-link
publication. Windows uses guarded native opens, holds ancestors against rename and publishes
with a non-replacing rename. Interrupted writes do not occupy the final destination; hidden
temporary files left by a killed process are not completed reports. Unsupported publication
semantics fail without a permissive fallback.

Linux: use `umask 077` and private 0700 directories. Windows: restrict the directory ACL to the
operator; files and SQLite inherit it. Existing database permissions are not changed. SQLite is
trusted single-user local state; the CLI rejects linked database paths, but this milestone does
not add database authentication or defend against an administrator replacing application state.
Errors use fixed categories without including source text, rationale or personnel values.

## Inspect, select and prepare

The following PowerShell commands are each one line. Replace `ANALYSIS_ID` and `sha256:FULL_HASH`
with explicit identities. Linux uses the same Python commands with `/` paths; create the private
directory with `umask 077; mkdir -p data/capabilities/workflow`.

```powershell
New-Item -ItemType Directory -Force data/capabilities/workflow | Out-Null
python -m tendercite.cli.capability_workflow --database data/tendercite.db overview --analysis-id ANALYSIS_ID --snapshot-hash sha256:FULL_HASH --output data/capabilities/workflow/overview.json
code data/capabilities/workflow/overview.json
```

Inspect original/effective statements, category/type, source references and grounding, current
review event/digest, snapshot period/catalog and mapping status. `mapping_preparation_eligible`
means only that a current accepted review and VERIFIED_QUOTE exist. It does **not** label a finding
skill-matchable. The root `skill_relevance` explicitly requires operator selection. Deadlines,
financial thresholds, insurance, certificates and other obligations often need entirely different
evidence; the workflow never converts them into skills or verifies their fulfillment.

Create a UTF-8 `selection.json` manually in the private directory, using IDs from the overview:

```json
{
  "schema_version": "capability-workflow-mapping-selection-1",
  "analysis_id": "synthetic-analysis",
  "finding_id": "synthetic-finding",
  "snapshot_hash": "sha256:e15ea7ea84176a4a9edf4bce710ede9008b620aa8329ddc14cb799354e9d6134",
  "operator": "ALL_OF",
  "skill_ids": ["00000000-0000-0000-0000-000000000065"],
  "rationale": "Operator explicitly associates the PostgreSQL catalog UUID",
  "operator_alias": "demo_operator"
}
```

```powershell
python -m tendercite.cli.capability_workflow --database data/tendercite.db prepare --input data/capabilities/workflow/selection.json --output data/capabilities/workflow/draft.json
code data/capabilities/workflow/draft.json
```

One to 100 UUIDs must be unique and present in the selected catalog; they are ordered canonically.
Rationale is bounded to 2,000 characters. The optional alias is non-personal ASCII letters,
digits, underscores or hyphens. No UUIDs are suggested from labels, embeddings or an LLM.

## Explicit lifecycle and matching

Use the existing 4.2b CLI for mutations. It can emit reviewed text and rationale; redirect its
output to private files instead of displaying it. The new workflow CLI's ordinary stdout remains
metadata only. Approval inputs explicitly name mapping ID, expected revision, current review
event and digest, rationale and optional alias; never reuse an old approval after a review.

```powershell
python -m tendercite.cli.capability_mappings --database data/tendercite.db create --input data/capabilities/workflow/draft.json > data/capabilities/workflow/create-result.json
python -m tendercite.cli.capability_mappings --database data/tendercite.db inspect --mapping-id MAPPING_UUID > data/capabilities/workflow/mapping-inspection.json
python -m tendercite.cli.capability_mappings --database data/tendercite.db approve --input data/capabilities/workflow/approval.json > data/capabilities/workflow/approval-result.json
python -m tendercite.cli.capability_mappings --database data/tendercite.db history --mapping-id MAPPING_UUID > data/capabilities/workflow/mapping-history.json
```

Create `assessment-selection.json` with the version below. Finding IDs must belong to the analysis,
be unique and number at most 100. An empty array is an explicit no-op report, not "all findings".

```json
{
  "schema_version": "capability-workflow-assessment-selection-1",
  "analysis_id": "synthetic-analysis",
  "snapshot_hash": "sha256:e15ea7ea84176a4a9edf4bce710ede9008b620aa8329ddc14cb799354e9d6134",
  "finding_ids": ["synthetic-finding"]
}
```

```powershell
python -m tendercite.cli.capability_workflow --database data/tendercite.db report --input data/capabilities/workflow/assessment-selection.json --output data/capabilities/workflow/assessment.json
code data/capabilities/workflow/assessment.json
```

Only current APPROVED, review-bound, catalog-valid mappings participate. DRAFT, REVOKED, STALE and
NOT_FOUND remain distinct, with existing 4.2b unavailable reasons and 4.2a NOT_EVALUATED reasons.
There is no older-approval or other-snapshot fallback. `not_eligible_for_matching` counts selected
NOT_EVALUATED rows, including missing mappings; it is not a determination of bidder ineligibility.
Zero individual counts on such a row mean no matching was performed. Evaluated rows count
individual ALL/SOME/NONE required assignments; individuals are never combined into a team.

Reporting is deterministic for identical loaded state: findings, skills and individuals are
ordered by ID/UUID and no generation timestamp is added. It is a read snapshot, not a persisted
decision or a guarantee that subsequent review changes will leave its approvals current.

After a new MODIFY, CONFIRM or REJECT, an old approval is derived STALE without rewriting history.
Inspect the latest review binding; explicitly revise into a new DRAFT, inspect it, and reapprove
only if the current finding remains eligible. Revocation is a separate explicit action:

```powershell
python -m tendercite.cli.capability_mappings --database data/tendercite.db revise --input data/capabilities/workflow/revision.json > data/capabilities/workflow/revision-result.json
python -m tendercite.cli.capability_mappings --database data/tendercite.db approve --input data/capabilities/workflow/reapproval.json > data/capabilities/workflow/reapproval-result.json
python -m tendercite.cli.capability_mappings --database data/tendercite.db revoke --input data/capabilities/workflow/revocation.json > data/capabilities/workflow/revocation-result.json
```

## Complete offline synthetic example

The executable example creates a fresh disposable SQLite database, a self-authored PostgreSQL
finding and the exact pinned Team Capacity Engine synthetic fixture. It performs real local CLI
subprocesses: overview → explicit selection → prepare → create → simulated explicit approval →
report → simulated changed review → stale report. It also leaves revision/reapproval/revocation
inputs for separately invoking those operations. **Simulated reviews and approvals are not genuine
human review or professional evidence.** No real people, tender documents or providers are used.

Linux, using a fresh directory:

```sh
umask 077
.venv/bin/python -m examples.local_capability_workflow --directory data/capabilities/workflow/demo
.venv/bin/python -m tendercite.cli.capability_mappings --database data/capabilities/workflow/demo/synthetic.db revise --input data/capabilities/workflow/demo/revision.json > data/capabilities/workflow/demo/revision-result.json
.venv/bin/python -m tendercite.cli.capability_mappings --database data/capabilities/workflow/demo/synthetic.db approve --input data/capabilities/workflow/demo/reapproval.json > data/capabilities/workflow/demo/reapproval-result.json
.venv/bin/python -m tendercite.cli.capability_workflow --database data/capabilities/workflow/demo/synthetic.db report --input data/capabilities/workflow/demo/assessment-selection.json --output data/capabilities/workflow/demo/reapproved-assessment.json
```

PowerShell, with a private directory ACL (each command one line):

```powershell
.venv\Scripts\python.exe -m examples.local_capability_workflow --directory data/capabilities/workflow/demo
.venv\Scripts\python.exe -m tendercite.cli.capability_mappings --database data/capabilities/workflow/demo/synthetic.db revise --input data/capabilities/workflow/demo/revision.json > data/capabilities/workflow/demo/revision-result.json
.venv\Scripts\python.exe -m tendercite.cli.capability_mappings --database data/capabilities/workflow/demo/synthetic.db approve --input data/capabilities/workflow/demo/reapproval.json > data/capabilities/workflow/demo/reapproval-result.json
.venv\Scripts\python.exe -m tendercite.cli.capability_workflow --database data/capabilities/workflow/demo/synthetic.db report --input data/capabilities/workflow/demo/assessment-selection.json --output data/capabilities/workflow/demo/reapproved-assessment.json
```

The approved PostgreSQL report has two individuals with all required assignments recorded and one
with no required assignment recorded. The stale report evaluates none. With Python AND PostgreSQL
explicitly selected, the fixture yields one ALL, one SOME and one NONE; these are internal
assignment counts, not measured tender qualification. Reuse a new directory for another demo.

## Evidence boundaries and remaining limitations

Reports distinguish source quotation occurrence, human requirement interpretation, operator
mapping approval, internal workforce assignments and **independent qualification evidence**.
Levels one through four do not establish the fifth. Qualification remains NOT_VERIFIED, capacity
NOT_EVALUATED and reviewed meaning NOT_ASSESSED. Missing assignments do not prove absent skills.
There is no experience-duration, certificate/reference, insurance, bidder eligibility, award
fulfillment, contractual availability, team feasibility or Go/No-Go inference; no scores or ranks.

Aliases are declarations, not authentication. Hashes detect content identity, not source truth.
Private exports are point-in-time support artifacts; operators must regenerate them after review
or mapping changes. The supplied review/source flags are reused, not reparsed or semantically
reverified by reporting. No evaluation-format bridge, multi-user authorization, new API/UI,
capacity scheduling, automated mappings or stronger qualification evidence is introduced.
