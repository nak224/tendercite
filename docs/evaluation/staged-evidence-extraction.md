# Staged evidence extraction and consolidation (3B-d4)

This evaluation-only method partitions all parseable family PDFs, extracts atomic findings,
consolidates them offline, and optionally synthesizes a shared `QAAnswer`. It has new prompts
and schemas: this is **not an identical-prompt comparison** with E5-RAG or Full-Context.
Production retrieval, APIs, original questions/responses and annotations are unchanged.
Codex and CI make **zero paid API calls**.

## Observed source preparation, 2026-10-12

The pinned local acquisition and recorded E5 corpus were available:

- Dataset revision `f2b0856ef0a5c327afb4f40c7cde5dd44c8d56ea`;
  family `BSGEE_2025-002_School_Information_Systems`; nine eligible original questions.
- Three parseable PDFs: **137 pages / 270,244 extracted characters**. The encrypted fourth
  PDF and its three dependent questions retain their original exclusions.
- Target ID: `BSGEE_2025-002_School_Information_Systems-df0f34c8`.
- A real **unpriced** source preview produced **54 extraction batches + 1 optional synthesis
  request**: 23 specification batches, 9 annex batches and 22 framework-contract batches.
  Conservative estimated extraction inputs ranged from 9,743 to 15,956 tokens, totaling
  782,183 including repeated prompt/schema overhead. These are UTF-8-byte estimates,
  **not provider-reported token usage**.

Ignored local artifacts: `staged-evidence-inventory-1/source-inventory.json` and
`staged-evidence-partition-preview-1/partition-preview.json` under `data/evaluation/external/`.
Exact source text, hashes and prepared payloads remain local.

The operator's original ledger, paid responses, COMPLETE human review and populated pricing
configuration are absent here. No cost or balance was invented. The operator reported roughly
1.230299 USD committed / 18.769701 USD remaining; only their real ledger can establish those
amounts. Staged extraction/synthesis, recall, semantic accuracy and provider performance are
**unmeasured**. The new prompts have not been exercised against a real provider.

## Deterministic workflow

`evaluation.staged_evidence` reuses provenance-checked `load_comparison_corpus`: acquisition
bytes/hashes, eligibility, pinned E5 metadata, production PDF parsing, chunk reconstruction
and stable document IDs are checked again. E5 is not rerun. All three parseable PDFs contribute;
upstream answers, expected references and reviewed spans never select source pages.

Optional staged settings are in `evaluation/staged-evidence-config.example.json`:

| Setting | Default |
| --- | ---: |
| Maximum estimated extraction input, including complete payload/schema overhead | 16,000 |
| Maximum distinct pages per batch | 6 |
| Extraction completion limit, including reasoning | 4,096 |
| Maximum estimated synthesis input, including complete payload/schema overhead | 32,000 |
| Synthesis completion limit, including reasoning | 4,096 |
| Paid calls per invocation | 1; explicit maximum 3 |

Ordering is deterministic by original filename and 1-based PDF page. Batches stay within one
document. Whole pages remain intact when they fit; oversized pages split at deterministic
character boundaries, preferably whitespace. Every character is retained, including whitespace
and empty pages. Segments preserve filename, document ID, PDF hash, page, source ID, offsets
and text hash. Every page appears in the coverage inventory with all assigned batches.
Disjoint split segments can miss evidence spanning a boundary; extraction is not exhaustive.

`staged-evidence-plan-1` records configuration/source hashes, batch IDs, ordering, page ranges,
payload hashes and prompt/schema versions. `partition` uses documented Sol/default estimator
parameters and emits a separate **UNPRICED_PREVIEW**, which cannot be loaded for execution.
A priced plan requires the existing valid comparison configuration; configuration changes may
change partitioning. Continue with the same saved immutable plan.

Extraction uses `staged-legal-extraction-1` / `staged-atomic-evidence-1`: subject/instrument,
exact written identifier, stated purpose, relationship, evidence and uncertainty. `LEGAL_REFERENCE`
is the first specialization; `OTHER` keeps the atomic record reusable. Separate provisions and
purposes remain separate. A synthetic response example is:

```json
{
  "question_id": "synthetic-legal-question",
  "batch_id": "batch-000000000000000000000000",
  "status": "FINDINGS",
  "findings": [{
    "finding_id": "finding-1",
    "kind": "LEGAL_REFERENCE",
    "subject": "Synthetic Act",
    "exact_identifier": "Synthetic Act section 4",
    "purpose": "Require two bidder references",
    "relationship": "OBLIGATION",
    "evidence": [{
      "source_id": "synthetic-doc:page:1:chars:0-64",
      "document_id": "synthetic-doc",
      "pdf_sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
      "page_number": 1,
      "quote": "Under Synthetic Act section 4, bidders must submit two references."
    }],
    "uncertainty": null
  }],
  "no_findings_reason": null
}
```

The synthetic identifiers/hashes above are illustrative; real responses must echo supplied
sources. A legitimate empty batch explicitly returns `NO_RELEVANT_FINDINGS`, an empty list
and a reason. Failed or missing requests never count as empty successes.

Quotes must occur **verbatim** in the actual supplied segment and stored PDF page, and pass
TenderCite's source validator. Wrong document/page/hash/source IDs, missing citations and
duplicate finding IDs are reported. Invalid batches retain raw records but are quarantined
from consolidation. Quote occurrence does not validate the interpretation or legal identity.

Offline `staged-evidence-consolidation-1` normalizes Unicode/whitespace and subject-name case;
identifiers retain case so `(a)` and `(A)` stay distinct. There is no fuzzy/legal alias inference.
Identical findings merge with every citation and batch/attempt origin preserved. Different
purposes, relationships and uncertainty remain separate. Potential relationship conflicts and
ambiguous identifier/name combinations are flagged for human review, not silently resolved.
Conservative grouping can under-merge; individual raw extraction records remain available.

Optional `staged-evidence-synthesis-1` uses the shared answer schema and only validated findings
and supporting quotes. It requires every extraction batch to have completed successfully.
Oversized synthesis context fails preflight; no findings are silently removed. Its citations
must reference supplied document/page evidence; `UNABLE_TO_ANSWER` remains distinct from an
answer lacking evidence. Semantic correctness/completeness still require human review.

## Shared budget and execution safeguards

The existing UTF-8-byte estimator, explicit prices and safety factors are unchanged. Input
estimates include both strict-schema copies and the complete wire payload. Extraction estimates
use prepared requests. Optional synthesis initially uses its configured input ceiling plus
maximum completion tokens; the actual payload is built/rechecked after validated extraction.

Execution requires the **original existing**
`data/evaluation/external/cloud-llm-experiment/ledger.json`, including its original provider,
model, provenance, prices and prior-spend binding. There is no new independent ledger/reset.
Offline planning can inspect an absent ledger but cannot create one. Never seed the reported
balance, copy a test ledger, delete entries or alter prices to make a run fit.

`--execute`, `--confirm-transmission`, exact question selection, valid `TENDERCITE_LLM_API_KEY`,
official OpenAI/Sol configuration, feasible preflight and bounded batch selection are mandatory.
The full planned count and conservative costs print before transmission. Every request has
a unique attempt and durable reservation under the original OS lock and **20 USD global cap**.
Step namespaces cannot collide with `e5_rag`/`full_context`. Pair execution, original truncation
retries, manual quota reconciliation and legacy records remain supported.

Continuation revalidates saved outputs, skips completed batches and sends at most the selected
limit. It stops on truncation, invalid evidence, uncertain usage, budget problems or provider
errors. Any failed batch blocks that plan; staged failed-step retries are not implemented.
There are no automatic retries, output-cap increases, model fallbacks or schema weakening.
An interrupted reservation remains committed and blocks further execution pending accounting.
The older manual reconciliation remains restricted to its supported quota incident; arbitrary
staged failures are not silently reconciled as zero cost.

Raw responses/usage, reasoning/cache counts, request IDs, latency, fees and validation stay local.
Reasoning is a subset of completion tokens and is not double-charged. Failed but billable calls
remain counted; missing usage stays uncertain. Usage-derived cost is not an invoice. Configure
provider-side project controls: local estimates are safeguards, not guaranteed billing caps.

## One-line PowerShell commands

Run from the repository root with its existing `.venv` and original operator ledger. Set paths
to the existing acquisition/retrieval and populated comparison config. The committed example
contains no prices or secrets. Every output directory must be fresh.

```powershell
$questionId = 'BSGEE_2025-002_School_Information_Systems-df0f34c8'
$corpusDir = '.\data\evaluation\external\eu-tenders-qa-f2b0856-school-systems'
$retrievalFile = '.\data\evaluation\external\document-retrieval-run-2\results.json'
$comparisonConfig = '.\data\evaluation\external\cloud-llm-comparison\config.json'
$stagedPlanDir = '.\data\evaluation\external\staged-evidence-plan-1'
```

Source inventory and unpriced preview (no key, ledger or provider call required):

```powershell
.\.venv\Scripts\python.exe -m evaluation.staged_evidence inventory --acquisition-dir $corpusDir --retrieval-results $retrievalFile --output-dir .\data\evaluation\external\staged-evidence-inventory-operator-1
.\.venv\Scripts\python.exe -m evaluation.staged_evidence partition --question-id $questionId --stage-config .\evaluation\staged-evidence-config.example.json --acquisition-dir $corpusDir --retrieval-results $retrievalFile --output-dir .\data\evaluation\external\staged-evidence-preview-operator-1
```

**First priced full-plan dry-run and inspection**, using original rate assumptions:

```powershell
.\.venv\Scripts\python.exe -m evaluation.staged_evidence plan --dry-run --question-id $questionId --config $comparisonConfig --stage-config .\evaluation\staged-evidence-config.example.json --acquisition-dir $corpusDir --retrieval-results $retrievalFile --output-dir $stagedPlanDir
.\.venv\Scripts\python.exe -m evaluation.staged_evidence inspect --plan-dir $stagedPlanDir --acquisition-dir $corpusDir --retrieval-results $retrievalFile --output-dir .\data\evaluation\external\staged-evidence-inspection-1
code (Join-Path $stagedPlanDir 'plan.json') (Join-Path $stagedPlanDir 'inspection.json')
```

Select the first exact batch and dry-run only that request:

```powershell
$batchId = (Get-Content (Join-Path $stagedPlanDir 'plan.json') -Raw | ConvertFrom-Json).batches[0].batch_id
.\.venv\Scripts\python.exe -m evaluation.staged_evidence extract --dry-run --question-id $questionId --plan-dir $stagedPlanDir --batch-id $batchId --max-requests 1 --acquisition-dir $corpusDir --retrieval-results $retrievalFile --output-dir .\data\evaluation\external\staged-evidence-first-batch-dry-1
```

**Operator only, after reviewing preflight and confirming document-transmission rights:** set
the session key securely, then execute exactly one extraction request:

```powershell
$env:TENDERCITE_LLM_API_KEY = [System.Net.NetworkCredential]::new('', (Read-Host 'OpenAI API key' -AsSecureString)).Password
.\.venv\Scripts\python.exe -m evaluation.staged_evidence extract --execute --confirm-transmission --question-id $questionId --plan-dir $stagedPlanDir --batch-id $batchId --max-requests 1 --acquisition-dir $corpusDir --retrieval-results $retrievalFile --output-dir .\data\evaluation\external\staged-evidence-execution-1
```

Continue one remaining batch after inspecting success, deliberately choosing a new output
directory each invocation. `--max-requests 2` or `3` explicitly raises the bounded limit:

```powershell
.\.venv\Scripts\python.exe -m evaluation.staged_evidence extract --execute --confirm-transmission --question-id $questionId --plan-dir $stagedPlanDir --continue-remaining --max-requests 1 --acquisition-dir $corpusDir --retrieval-results $retrievalFile --output-dir .\data\evaluation\external\staged-evidence-execution-2
```

Offline consolidation and optional synthesis preflight; incomplete/oversized evidence fails:

```powershell
.\.venv\Scripts\python.exe -m evaluation.staged_evidence consolidate --plan-dir $stagedPlanDir --acquisition-dir $corpusDir --retrieval-results $retrievalFile --output-dir .\data\evaluation\external\staged-evidence-consolidation-1
code .\data\evaluation\external\staged-evidence-consolidation-1\consolidation.json
.\.venv\Scripts\python.exe -m evaluation.staged_evidence synthesize --dry-run --question-id $questionId --plan-dir $stagedPlanDir --consolidation-dir .\data\evaluation\external\staged-evidence-consolidation-1 --acquisition-dir $corpusDir --retrieval-results $retrievalFile --output-dir .\data\evaluation\external\staged-evidence-synthesis-dry-1
```

A separately authorized single synthesis call:

```powershell
.\.venv\Scripts\python.exe -m evaluation.staged_evidence synthesize --execute --confirm-transmission --question-id $questionId --plan-dir $stagedPlanDir --consolidation-dir .\data\evaluation\external\staged-evidence-consolidation-1 --acquisition-dir $corpusDir --retrieval-results $retrievalFile --output-dir .\data\evaluation\external\staged-evidence-synthesis-1
```

Offline comparison across all three methods, including incomplete Full-Context attempts:

```powershell
.\.venv\Scripts\python.exe -m evaluation.cloud_llm_reporting --staged-plan $stagedPlanDir --acquisition-dir $corpusDir --retrieval-results $retrievalFile --output-dir .\data\evaluation\external\staged-evidence-comparison-1
code .\data\evaluation\external\staged-evidence-comparison-1\comparison.md .\data\evaluation\external\staged-evidence-comparison-1\attempts.csv .\data\evaluation\external\staged-evidence-comparison-1\comparison.json
```

Reports retain every attempt/cost. Existing `--latest-valid` selection remains explicit.
Extraction completion is separate from optional synthesis completion. Per-stage/request tokens,
measured request latency, costs, cost per accepted raw finding, duplicates, citations, issues and
complete-page inventory are reported. Wall time across invocations is unmeasured/null. Unknown
usage categories and uncertain costs stay null, with known subtotals labeled separately.

Add `--annotations $annotations --validation $validation` only for existing hash-bound COMPLETE
source-span review. It is freshly revalidated against acquisition/PDF provenance; missing,
partial or stale review never becomes gold. Counts/recall require a complete verbatim reviewed
quote in original page coverage, accepted extraction evidence or consolidated evidence on the
same document/page. Missing spans/documents and incomplete runs stay explicit. This measures
text occurrence, not factual/legal correctness or independently verified answer accuracy.

Optional `--human-review $humanReviewFile` reuses separate hash-bound human verdicts, now also
accepting `staged_extraction`/`staged_synthesis`, exact attempt ID and response-file hash. Human
declarations never overwrite model outputs. See [the comparison guide](cloud-llm-comparison.md).

## Rights and validation limits

Source text, prompts, responses, annotations and reports stay under ignored
`data/evaluation/external/`; never force-add them. Dataset availability does not establish
document re-use/transmission rights. Reuse the [acquisition provenance workflow](external-benchmark-pilot.md)
and obtain operator confirmation before transmission. API keys are never logged/persisted,
including echoed errors.

Offline tests use generated PDFs, synthetic findings/review declarations, artificial prices and
mock HTTP. They cover source conservation, identities, empty/failed outputs, strict schemas,
quotes, consolidation/conflicts, synthesis exposure/budgets, accounting, shared ledger, duplicate
prevention, continuation, interruptions, legacy workflows, gold and human reviews, and ignored
storage. Windows commands/locking need operator validation. Real execution, semantic review and
provider billing reconciliation remain pending. The small English pilot does not establish
German quality, exhaustive tender extraction or production readiness.

Local validation: `HF_HUB_OFFLINE=1 pytest` passed **616 tests**, with one optional live TED
test skipped; Ruff lint and formatting passed. Docker built with the managed proxy and trusted
CA mount. An additional coverage-instrumented run hit the existing Streamlit smoke test's
three-second timeout (615 passed); the unchanged UI tests passed in isolation and in the full
normal run. GitHub CI must be checked on this PR's final revision.
