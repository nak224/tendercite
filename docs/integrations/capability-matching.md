# Evidence-aware candidate discovery (4.2a)

This service compares explicitly mapped skill UUIDs with one imported Team Capacity Engine
snapshot. It discovers **recorded assignments**, not verified professional experience,
certifications, bidder qualification or committed availability. It creates no review, assessment,
allocation or Go/No-Go decision. The [schema-v1 importer](team-capacity.md) remains unchanged.

## Contracts and service boundaries

`tendercite.domain.capability_matching` defines separate versioned Pydantic contracts:

| Contract | Version | Purpose |
| --- | --- | --- |
| `CapabilityMatchingRequest` | `capability-matching-request-1` | Analysis ID, explicit snapshot hash, selected finding IDs and operator mappings |
| `RequirementSkillMapping` | `requirement-skill-mapping-1` | One finding, mandatory `ALL_OF` operator and non-empty unique catalog UUIDs |
| `CandidateSkillCoverage` | `candidate-skill-coverage-1` | Per-person required, assigned, matched and missing UUIDs, with original assignment source/proficiency |
| `EvidenceLimitation` | `capability-evidence-limitation-1` | Explicit limits of the available evidence |
| `CapabilityMatchingResult` | `capability-matching-result-1` | Scoped findings, original/reviewed values, sources, snapshot provenance and algorithm version |

The algorithm is `explicit-uuid-membership-1`. Unknown request fields, unsupported versions,
duplicate selections/mappings/skill UUIDs, empty skill sets, unmapped selections in mapping records,
alternative operators and proficiency thresholds are rejected. At most 100 findings may be
selected. A selected finding may deliberately have no mapping; it remains visible as not evaluated.

`match_from_repository(request, repository)` calls existing `get_analysis`, `get_finding` and
`get_capability_snapshot` methods. Current reviewed findings come from `get_finding`; the original
findings embedded in `AnalysisRun` are not substituted for the review overlay. Missing data or
another analysis's finding is an error, never a negative skill judgment. The selected finding
must also belong to the original analysis's finding inventory. Exactly one snapshot is loaded by
hash; there is no latest-state fallback, multi-snapshot merge, extra table or persisted matching run.

`match_capabilities(request, *, analysis, findings, snapshot)` is the pure core over already loaded
data. It validates scope, mappings and snapshot integrity, checks eligibility, compares UUID sets
and constructs the result. It has no clock, network, model, database or mutation operations.
Pure callers supply the existing review/grounding state; the core does not independently reparse
PDFs or establish human review. The repository adapter provides those states from stored records.
Use `SqliteRepository(path, read_only=True)` for inspection without database initialization.

## Selection, mappings and eligibility

Operators select an existing analysis and finding IDs, inspect their current reviewed values,
then explicitly associate each relevant finding with UUIDs from the chosen snapshot catalog.
The `ALL_OF` operator must be supplied. Mappings are operator interpretations; their semantic
correctness is not independently verified by this service. No words are parsed into skills, no
names are compared and no alternatives, synonyms, equivalence or proficiency thresholds are inferred.

Only `CONFIRMED`/`MODIFIED` findings with `VERIFIED_QUOTE` grounding and an explicit mapping are
evaluated. Selected unreviewed, rejected, invalidly grounded and unmapped findings remain in the
result with `NOT_EVALUATED`, no candidates and every applicable reason. Reasons distinguish
`UNREVIEWED_FINDING`, `REJECTED_FINDING`, `INVALID_SOURCE_QUOTE`, `MISSING_SOURCE_EVIDENCE` and
`NO_EXPLICIT_SKILL_MAPPING`. A `MODIFIED` state without a reviewed value is inconsistent and fails.

Original and effective reviewed statement/category/type, exact source references, review status,
grounding status and the explicit mapping are retained. A verified source quotation establishes
its occurrence, not that a changed interpretation is directly established by it. Reviewed meaning
is therefore `NOT_ASSESSED`. Changed reviewed values receive a specific limitation, including
values modified earlier and subsequently `CONFIRMED`; review never proves workforce fulfillment.

## Exact matching rules

For each eligible finding and each member:

1. Required UUIDs are the explicit `ALL_OF` mapping.
2. Recorded UUIDs are that person's assignments in the selected snapshot.
3. Matched UUIDs are their intersection; missing assignments are their set difference.
4. Coverage is `ALL_REQUIRED_SKILLS_RECORDED` if none are missing,
   `SOME_REQUIRED_SKILLS_RECORDED` if at least one matches, otherwise
   `NO_REQUIRED_SKILLS_RECORDED`.

Every member remains visible, including members with no recorded assignments. Their engagement
type, all original assigned UUIDs, original source labels and nullable proficiency are preserved.
Engagement/proficiency affect neither coverage nor qualification. A missing assignment means no
record, not that the person lacks the skill.

The finding summary has `coverage_basis: INDIVIDUAL_ASSIGNMENTS`: ALL means at least one person
has the complete set, SOME means partial individual coverage exists with no complete individual,
and NONE means nobody has a mapped assignment. Skills on different people are never combined into
an ALL_OF team. No candidates are selected, ranked or ordered by coverage/proficiency/capacity.
Findings sort by finding ID; people and UUID collections sort by UUID. Original request/mapping
order is retained for audit. Identical loaded inputs produce identical serialized results; later
human reviews or a different snapshot are different inputs, even for the same analysis ID.
Retain the complete result wrapper when exporting: snapshot and algorithm provenance are shared
at the top level rather than repeated in every per-person record.

The producer's v1 catalog contains exactly the skills assigned somewhere in that snapshot.
Every mapped UUID must exist in it. Consequently, a non-empty valid mapping already has at least
one recorded assignment somewhere; global unassigned-skill discovery is not supported by v1.
An empty workforce also has an empty catalog: unmapped findings can be reported as not evaluated,
but a non-empty mapping is rejected as `UNKNOWN_CATALOG_SKILL_UUID`. It is never repaired into a
zero-coverage conclusion. A skill missing from one person can still be assigned to another.

## Conclusions and limitations

All result, finding and candidate qualification fields remain **`NOT_VERIFIED`**. Capacity is
always **`NOT_EVALUATED`**. Current models have no independently verified workforce qualification
artifact that could justify an upgrade. Coverage is not `AssessmentStatus.FULFILLED` and does not
overwrite an existing human-managed assessment, regardless of its current status.

| Input | Supported conclusion | Unsupported conclusion |
| --- | --- | --- |
| PostgreSQL UUID assigned | Internal skill record present | Verified professional experience |
| Proficiency 5 and a three-year requirement | Original proficiency retained | Three years of experience established |
| Kubernetes assignment and certificate requirement | Explicit UUID assignment present | Certified Kubernetes Administrator verified |
| Three members with positive period balances | Snapshot identity/period retained | Three committed developers for 30 hours/week |
| Missing assignment | No record for that person in this snapshot | Person lacks the skill or is ineligible |

Capacity values are not scored or included in candidate coverage. Their original known/unknown,
zero/negative, daily overload and unreserved semantics remain in the immutable selected snapshot.
The result retains its hash, assessment period, schema/source and seven source limitations; callers
can retrieve that exact snapshot later. No positive balance implies daily/intraday feasibility,
reservation, historical availability or contractual commitment. There is no fit percentage,
confidence score, automatic qualification, skill extraction, UI or public endpoint.

Results contain pseudonymous workforce IDs and potentially sensitive tender quotations. Keep
them in approved private local storage; the service does not log or persist them. A hash proves
content consistency, not source authenticity or workforce truth. Multiple repository reads do
not promise a transaction-wide point-in-time view of concurrent human reviews; determinism applies
to the recorded loaded states. No matching history or authenticated operator identity is added.

## Executable synthetic example

The [complete example](../../examples/capability_matching.py) creates synthetic analysis/finding
models and reads the existing pinned workforce fixture. It represents a reviewed/grounded input;
it does not perform or fabricate an actual human review, write a database or call a model.
The key operation is:

```python
result = match_capabilities(request, analysis=analysis, findings=[finding], snapshot=snapshot)
assert result.findings[0].qualification == "NOT_VERIFIED"
assert result.capacity_evaluation == "NOT_EVALUATED"
```

Run from the TenderCite root after the existing Python 3.12 package setup:

```powershell
.\.venv\Scripts\python.exe examples\capability_matching.py
```

```sh
.venv/bin/python examples/capability_matching.py
```

It prints the selected hash and three individual coverage statuses: ALL, ALL, NONE. Experience
duration remains unverified and capacity is not evaluated. It works without LLM credentials and
creates no data directory. To use existing stored data in your own local Python workflow, construct
the same explicit request and pass a read-only repository to `match_from_repository`.

## Validation and next milestone

Offline tests use synthetic findings, synthetic review/assessment records and the unchanged pinned
producer example, plus independently hashed synthetic variants. They cover ALL_OF, partial/missing
coverage, empty workforce/catalogs, duplicates, bad scope, current reviews, changed interpretation,
historical hashes, stable ordering, corruption and unsupported claims. Repository tests enable
SQLite `query_only`, forbid write methods, HTTP/provider calls and embedding construction, then
compare every original table, database bytes and Go/No-Go rows. The example runs as a subprocess.

Local validation on 2026-10-14 passed **937 tests**, with one optional live TED test skipped,
including 43 new matching tests; total test coverage was 96%. Ruff lint/format passed (166 Python
files). The first local run reached the existing UI test's three-second cold-import timeout.
Preloading its installed dataframe dependencies allowed the complete suite to pass with the
original assertions and timeout unchanged:

```sh
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 .venv/bin/python -c 'import pandas, pyarrow, pytest; raise SystemExit(pytest.main(["--cov=tendercite", "--cov-report=term-missing"]))'
```

Milestone 4.2b can consume these versioned coverage records and provenance for the next explicitly
scoped evidence/review workflow. Stronger qualification or capacity conclusions will require
separate evidence contracts and validation rules; they must preserve the current unknown/unverified
distinctions and human decision boundaries. The exact 4.2b feature scope is not defined by this
request, and none is implemented here.
