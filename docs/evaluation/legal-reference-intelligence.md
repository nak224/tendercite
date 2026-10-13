# Offline legal-reference review (3B-d5)

The existing offline comparison reporter now derives `staged-legal-reference-layer-1` from
the unchanged `staged-evidence-consolidation-1`. No extraction is rerun, no provider is
contacted and no reservation or ledger write occurs. Production behavior, original questions,
pricing, estimator, prompts, schemas and saved step identities are unchanged.

## Identity, findings and evidence

`legal-references.json` contains canonical reference identities and separately auditable
associated findings. A source-identified instrument can have several requirements, definitions
and provisions. Article 4 is a `SPECIFIC_PROVISION` with its own stable ID and parent reference;
it does not disappear when instrument aliases merge. Every associated finding retains the
original atomic records, consolidated ID, original names/identifiers, purposes, relationships,
uncertainty, batch/attempt IDs, response-file hashes and exact PDF citations.

Reference specificity, mention role and original relationship are separate dimensions:

| Dimension | Values / interpretation |
| --- | --- |
| Reference identity | `IDENTIFIED_INSTRUMENT`, `UNSPECIFIED_LEGAL_REFERENCE`, `UNRESOLVED_REFERENCE`; provisions retain `SPECIFIC_PROVISION` separately |
| Mention role | Operative requirement, definition, procedural condition, exception, historical/repealed mention, contextual reference or unresolved conflicting signals |
| Original relationship | The original model's `OBLIGATION`, `DEFINITION`, `EXCLUSION`, `REFERENCE`, `PROCEDURE`, `CONDITION` or `OTHER`; never overwritten |
| Citation status | `VERIFIED_QUOTE`, `CITATION_INVALID`; successful empty batches display `NO_CITATIONS_APPLICABLE` in reports |
| Semantic status | `SEMANTIC_REVIEW_PENDING`, `HUMAN_VERIFIED_MEANING`, `HUMAN_REJECTED_MEANING`, `HUMAN_UNCERTAIN_MEANING` |

Deterministic classification is an inspectable source-based **candidate**, not an independently
verified legal conclusion. Quote verification establishes occurrence, not meaning, applicability
or completeness. Source-span coverage remains in the separate benchmark-evidence section;
no legal semantic accuracy score is calculated.

## Conservative rules and limitations

`source-backed-legal-normalization-1` normalizes Unicode compatibility forms, whitespace,
line breaks and typographic quotes/dashes. Explicit numbered Regulation/Directive identifiers
(including German Verordnung/Richtlinie headings) normalize instrument type, stated jurisdiction
and number. Provision letters retain case: `(a)` and `(A)` remain distinct. Missing jurisdiction
is never filled in; conflicting markers and multiple instrument identities are not repaired.
Unsupported instrument formats/names remain unresolved, rather than acquiring identities from
legal knowledge. This small recognizer is not a comprehensive multilingual legal ontology.

Aliases merge only with exact source equivalence in an accepted citation: an immediate
parenthetical name/identifier, or an explicit “hereinafter”, “also known as”, “called” or
“im Folgenden” construction. Both names merely occurring on a page, similarity, upstream QA
answers and model memory are insufficient. Different source documents can contribute proof,
but every proof retains its original quotation/page and artifact origins. Other phrasing stays
unresolved until humans supply the separate mapping described below.
An end-of-title parenthetical can identify a single main instrument when the cited title starts
with that identifier and explicitly mentions another instrument only as repealed. The alias is
never assigned to that nearest repealed identifier. Ambiguous multi-instrument text stays unresolved.

For example, source text explicitly stating `Regulation (EU) 2016/679 (General Data Protection
Regulation)` permits the alias association. Without such evidence the name stays unresolved.
“FR” is not expanded from model knowledge. An explicit numbered instrument/FR equivalence
or reviewed mapping is needed; unspecified Union/member-state laws remain unspecified.
`repealing Directive 95/46/EC` classifies that distinct directive as a historical/repealed mention
**according to the cited text**. It does not establish current repeal status externally or turn
a title mention into an operative obligation. Contradictory historical/operative signals remain
flagged for human review.

`provision_of` requires an explicit provision-of-instrument construction in cited text;
`obligation_under` requires a stated under/pursuant-to/in-accordance-with relationship plus
obligation wording. Proven aliases can provide the instrument name for either rule.
`same_instrument_as` records the alias association and its proof. `condition_of`/`exception_to`
require an exact subordinate clause (whitespace normalization only for this relationship check)
inside a distinct cited parent statement on the same source page. A child must start with an
explicit conditional/exception connective. Multiple plausible parents remain unresolved.
Shared instruments/pages alone never authorize substantive links. Unsupported wording may
under-link; inspect `unresolved_links` rather than assuming an exception was absent.

Counts remain conservative: requirements per instrument count identified, operative candidate
findings separately; historical/unspecified mentions and human-rejected/uncertain meanings are
excluded. Alias-merge count is the number of distinct proven alias/reference associations,
not repeated mentions. Raw atomic counts include quarantined outputs. Source coverage retains
missing/failed batches and incomplete experiments, including all document/page inventories.

## Safe reuse of paid outputs

Use the **original existing** 20 USD ledger and saved 3B-d4 plan in their original local paths.
The reporter revalidates acquisition/PDF bytes, recorded retrieval provenance, saved plan and
request payload identities. Each historical response is checked against its ledger SHA-256,
source/configuration/payload hashes, and independently replayed through the original strict
response/citation validators. Provider usage is reconciled with the original recorded fee.
Missing/tampered/invalid outputs are quarantined; their recorded costs remain visible.

The old consolidation remains the report's `staged.evidence`; the new layer is separate under
`staged.legal_references` and in `legal-references.json`. Adding it never changes a synthesis
step ID, plan, response, budget or prompt/schema version. The empty-batch display correction
also leaves serialized historical validation untouched. Reporting holds the existing OS lock;
lock-owner metadata is separate from the immutable ledger. Full extraction/synthesis coverage
and semantic correctness cannot be inferred from a few successful batches.
Eligibility revalidation is read-only too; it no longer replaces the acquisition validation
report. One saved staged plan is replayed per report. Other plans' fees remain visible but their
token/source diagnostics require a separate report with their original saved plan.

The operator reported these three real pilots; their ledger/responses are **absent in this cloud
workspace**, so this milestone does not claim to have independently verified these values:

| Operator-reported pilot | Findings | Citations | Usage-derived USD |
| --- | ---: | --- | ---: |
| Specifications pp. 1–3 | 0 (`NO_RELEVANT_FINDINGS`) | No citations applicable | 0.013264 |
| Framework pp. 40–41 | 8 | 8/8 | 0.051023 |
| Specifications pp. 21–23 | 8 | 9/9 | 0.058132 |

The operator reported strict structured output and zero reasoning tokens in all three. No
token counts, estimates, balances or full-pipeline costs were invented here. When those original
files are present, per-attempt `estimated_vs_reported` contains the original conservative input
estimate, maximum output limit, reserved cost with margin, actual provider input/completion
tokens (reasoning is a subset) and usage-derived fee. All historical attempts remain visible;
these numbers are not invoices. Final billing needs the provider dashboard. No estimator or
pricing retuning, accuracy extrapolation or paid calls occur in this milestone.

## Offline PowerShell commands

Run from the repository root. Use the paths to the operator's existing acquisition, E5 results
and immutable staged plan; keep its recorded response directories and original ledger in place.
Each command is one line; choose a fresh ignored output directory each time. No key is needed.

```powershell
$corpusDir = '.\data\evaluation\external\eu-tenders-qa-f2b0856-school-systems'
$retrievalFile = '.\data\evaluation\external\document-retrieval-run-2\results.json'
$stagedPlanDir = '.\data\evaluation\external\staged-evidence-plan-1'
.\.venv\Scripts\python.exe -m evaluation.cloud_llm_reporting --staged-plan $stagedPlanDir --acquisition-dir $corpusDir --retrieval-results $retrievalFile --output-dir .\data\evaluation\external\legal-reference-review-1
code .\data\evaluation\external\legal-reference-review-1\legal-references.json .\data\evaluation\external\legal-reference-review-1\comparison.md .\data\evaluation\external\legal-reference-review-1\legal-meaning-review-template.json
```

An absent original plan/ledger fails clearly. Do not create a replacement ledger, seed costs,
rerun extractions or edit immutable paid outputs to make reporting succeed.

## Human meaning review and optional alias mappings

The generated `legal-meaning-review-template.json` uses the separate versioned schema
`legal-reference-meaning-reviews-1`. All rows start `DRAFT`, with no fabricated human verdicts.
Copy it to a local working file, inspect every original quote/page and requirement interpretation,
then have a distinct second human review it. Enter non-personal author/reviewer aliases, a
timezone-qualified timestamp and notes. Use `VERIFIED`, `REJECTED` or `UNCERTAIN`; leave
unreviewed rows `DRAFT`. Rejected/uncertain meaning does not overwrite extracted text.

```powershell
Copy-Item .\data\evaluation\external\legal-reference-review-1\legal-meaning-review-template.json .\data\evaluation\external\legal-meaning-review-local.json
code .\data\evaluation\external\legal-meaning-review-local.json
.\.venv\Scripts\python.exe -m evaluation.cloud_llm_reporting --staged-plan $stagedPlanDir --legal-meaning-review .\data\evaluation\external\legal-meaning-review-local.json --acquisition-dir $corpusDir --retrieval-results $retrievalFile --output-dir .\data\evaluation\external\legal-reference-reviewed-2
```

Meaning reviews require the exact target finding and **all** original response-file hashes.
The top-level `consolidation_sha256` is the SHA-256 of canonical JSON (the existing `digest`
helper), supplied by the template; it is not a hash of prettified JSON bytes. Provenance must
match exactly. Unknown targets, duplicate reviews, stale hashes and non-independent review
are rejected. Invalid citations cannot receive `HUMAN_VERIFIED_MEANING`. A human declaration
is displayed explicitly; it does not create a benchmark score or automatically resolve identity.

For source equivalence phrased outside the recognizer, use optional
`--legal-alias-map .\data\evaluation\external\legal-alias-map-local.json`. This separate schema
is `legal-reference-alias-map-1` with the same `consolidation_sha256` and `provenance`. Its
`aliases` records require `alias`, one supported explicit `instrument_identifier`, non-personal
`author_alias`/distinct `reviewer_alias`, `reviewed_by_human: true`, timezone-qualified
`reviewed_at`, non-empty `review_notes`, and `original_response_sha256s` pointing to the
original accepted artifacts the humans reviewed. Do not invent a human approval. Duplicate,
stale or unbound mappings fail; contradictory mappings are kept as review issues and never
silently merged. Review-file hashes appear in `comparison.json`.

For page inspection reuse the existing offline `staged_evidence inventory` command from the
[staged guide](staged-evidence-extraction.md). It exports exact parser text and page numbers.
All source-containing exports, annotations, mappings and raw paid responses remain under ignored
`data/evaluation/external/`. Do not force-add them. Keep original rights/provenance restrictions;
offline inspection does not authorize redistribution or transmission to a provider.

## Validation and remaining work

Tests use only original generated PDFs and synthetic historical response files. They cover
source-backed aliases/no-proof ambiguity, distinct articles/requirements, unresolved FR,
historical/operative conflicts, conditions/exceptions, cross-document grouping, incomplete/empty
batches, citation/hash/provenance failures, independent human review, old plans and cost history.
Reporting tests forbid HTTP clients, provider requests, reservations and ledger writes while
checking every original artifact byte after export. CI remains offline and downloads no models.

Actual operator artifact replay, semantic review, unsupported legal syntax, Windows command
validation and full-pipeline coverage remain open. A successful quote is not legal correctness,
exhaustive extraction or production readiness.

Local validation on 2026-10-13: `HF_HUB_OFFLINE=1 pytest` passed **666 tests**, with one
optional live TED test skipped. Ruff lint and formatting passed (145 Python files). The real
offline CLI revalidated the available acquisition/retrieval inputs, then stopped at the missing
original `staged-evidence-plan-1/plan.json`; the original experiment ledger is also absent.
Input manifests/validation/retrieval bytes stayed unchanged, no output report or ledger was
created, and no API call occurred. The ignored readiness record is
`data/evaluation/external/legal-reference-readiness-cloud-1/availability.json`.
