# Recorded external source-span retrieval (Milestone 3B-b2)

This offline evaluator scores recorded ranked E5 chunks against locally validated human
source-span annotations. It does not load embeddings, download models, rerun retrieval,
call an LLM or modify production behavior or original review labels. Source-span coverage
does not measure answer correctness, requirement extraction or general retrieval accuracy.

## Actual execution status: blocked, no scores

On 2026-10-11, the runner was invoked in this Codex environment with the requested files.
It returned exit code 1: `external-qa-source-spans-1/validation-draft-all.json` is missing.
The retained `review.json` is the initial template: nine eligible questions, **zero evidence
records**. The user's COMPLETE 9/9 reviewed artifacts are not present here. The completed
3B-a retrieval results and pinned acquisition are available, but cannot supply missing human
ground truth. No accuracy scores, scored per-question results, misses or structural-span
counts were generated. This is an input-availability blocker, not a model download failure.

The ignored `data/evaluation/external/external-source-span-metrics-1/results.json` records
the blocker and runtime/source checksums, with no `metrics` or `cases` keys. The prior 3B-a
document-level scores are not substituted for source-span scores. All four requested metrics
at **1, 3, 5, 10, 20 and 50** remain unmeasured in this environment.

The local eligible IDs below retain the prefix
`BSGEE_2025-002_School_Information_Systems-`. Every row is **NOT SCORED**; no verified spans
are present in the local template.

| ID suffix | Original question type | Local verified spans |
| --- | --- | --- |
| dc506b77 | Single-document lookup | 0 |
| 99401175 | Single-document lookup | 0 |
| 10671deb | Single-document reasoning | 0 |
| d6910bdf | Single-document reasoning | 0 |
| ab268553 | Single-document reasoning | 0 |
| df0f34c8 | Cross-document factual | 0 |
| 0a938a30 | Cross-document factual | 0 |
| a30e4536 | Implicit multi-hop | 0 |
| d8adf980 | Implicit multi-hop | 0 |

## Inputs and provenance checks

Dataset: `tmskss/eu-tenders-with-questions-for-agentic-checklist-filling`, revision
`f2b0856ef0a5c327afb4f40c7cde5dd44c8d56ea`, family
`BSGEE_2025-002_School_Information_Systems`. Recorded model:
`intfloat/multilingual-e5-small` at
`614241f622f53c4eeff9890bdc4f31cfecc418b3`. Its official download/loading verification belongs
to [3B-a](external-document-retrieval.md); no fresh loading or weight verification is claimed.

Available input SHA-256 values (the template hash is **not a reviewed gold hash**):

| Local artifact | SHA-256 |
| --- | --- |
| Initial review.json | d86aad1fb74ac76b89e1e5bdd1c8b8494a400e3c155c40427abaabc7851cb47b |
| document-retrieval-run-2/results.json | fdc72306abcb3794ec8198ed6ada19482596d3d21d08b7363fa9c22d040d5278 |
| Acquisition manifest.json | 9217cb8630e6e8ec23a98414c64d45da626f2e9a2acf287baee6284f185058db |
| Requested COMPLETE validation report | Unavailable |

A successful run requires all nine original eligible IDs, original questions/types/references,
the pinned acquisition family/revision and a COMPLETE source-span validation report. The review
file must match its recorded hash. The runner revalidates annotations/PDF provenance using the
existing [human review rules](external-qa-source-spans.md), then compares the saved report with
fresh validation in full. Stale reports and invalid/unverified support cannot supply gold spans.
Independent aliases are human declarations; software does not authenticate the reviewers.

The full production index is reconstructed from hash-checked PDF snapshots using the existing
parser and chunker, without embeddings or Chroma queries. Recorded configuration must be
**1,200 characters, 150 overlap, top-50 chunks**. Deterministic document/chunk IDs, PDF hashes,
page/chunk counts and full-family search scope must match 3B-a. Every recorded chunk must match
its reconstructed document, filename, page and exact stored chunk text; scores must be finite
and descending. This checks input consistency rather than authenticity of the historical model
execution. Successful local output records input/PDF artifact hashes and original environment.

## Metrics and structural diagnostics

All cutoffs use **recorded chunk rank**, including duplicate positions. A duplicate hit can
consume a rank but cannot increase the count of individually retrieved evidence spans. No
reranking, unique-document ranking, quote rewriting or assembly of adjacent chunks occurs.
Matching uses production `normalize_quote` (collapse whitespace, preserve case).

| Metric | Per-question definition |
| --- | --- |
| page_hit_at_k | Any top-k chunk matches a VERIFIED evidence document and page |
| source_span_hit_at_k | Any top-k chunk on the correct document/page contains a complete verified quote |
| all_source_documents_supported_at_k | At least one complete verified quote retrieved from each originally required document |
| verified_span_recall_at_k | Number of distinct verified evidence records retrieved / all verified evidence records |

Strict metrics retain **all validated VERIFIED spans**, including structurally impossible
single-chunk matches. Aggregates are question-macro averages, separately combined and by
upstream question type; span recall is averaged per question, not pooled over spans.

Before scoring, each span is checked against **all reconstructed indexed chunks**, not only
the recorded top-50 pool:

- `complete`: the complete normalized quote occurs in an indexed chunk on its document/page.
- `too_long`: no complete match exists and the original quote exceeds 1,200 characters.
- `split_across_chunks`: the quote occurs on the page, is within the size limit, but no single
  chunk contains it. Partial chunks are never joined to make a hit.
- `mapping_inconsistency`: a page/quote or index mapping is unavailable/inconsistent; scoring
  fails rather than publishing accuracy from invalid inputs.

Complete-match detection takes precedence over length because whitespace normalization can
reduce quote length. Diagnostics retain both raw/normalized lengths and matching chunk IDs.
The separate `scorable_diagnostic` reports span Hit@k and span recall using only `complete`
spans. Its aggregate denominator excludes questions with no scorable spans, with explicit
question/span counts; undefined denominators are null. It does not replace strict metrics
or reduce the original document set for the strict all-document metric.

Per-question local results preserve references/pages, verified-span counts, first matching
chunk ranks, retrieved/missed span IDs at each cutoff and structural reasons. Per-span misses
distinguish a match below the requested cutoff, a scorable span absent from the recorded
top-50 pool, and size/boundary limitations. Ranks beyond 50 are unknown. No source-span or
answer-correctness conclusions can be drawn until the actual reviewed inputs are scored.

## Reproduce on Windows (one-line PowerShell)

Run in the repository root with its configured virtual environment and the existing reviewed
files on your Windows machine. Keep the same acquisition manifest and recorded results that
the review template references. Do not regenerate/download them merely to bypass a hash error.

```powershell
.\.venv\Scripts\python.exe -m evaluation.external_source_span_retrieval --acquisition-dir .\data\evaluation\external\eu-tenders-qa-f2b0856-school-systems --annotations .\data\evaluation\external\external-qa-source-spans-1\review.json --validation-report .\data\evaluation\external\external-qa-source-spans-1\validation-draft-all.json --retrieval-results .\data\evaluation\external\document-retrieval-run-2\results.json --output-dir .\data\evaluation\external\external-source-span-metrics-windows-1
```

If a new validation report is required after genuine human review, use a fresh filename:

```powershell
.\.venv\Scripts\python.exe -m evaluation.validate_external_qa_spans .\data\evaluation\external\external-qa-source-spans-1\review.json --acquisition-dir .\data\evaluation\external\eu-tenders-qa-f2b0856-school-systems --retrieval-results .\data\evaluation\external\document-retrieval-run-2\results.json --report .\data\evaluation\external\external-qa-source-spans-1\validation-before-metrics-1.json
.\.venv\Scripts\python.exe -m evaluation.external_source_span_retrieval --acquisition-dir .\data\evaluation\external\eu-tenders-qa-f2b0856-school-systems --annotations .\data\evaluation\external\external-qa-source-spans-1\review.json --validation-report .\data\evaluation\external\external-qa-source-spans-1\validation-before-metrics-1.json --retrieval-results .\data\evaluation\external\document-retrieval-run-2\results.json --output-dir .\data\evaluation\external\external-source-span-metrics-windows-2
```

Success returns 0 and prints aggregate/structural summaries; blocked input validation returns
1 and writes a local failure record without scores. Use fresh output paths for every attempt.
The full JSON retains questions/quotes locally, so inspect it in VS Code and publish only a
rights-reviewed, source-free summary. All inputs/outputs remain confined to ignored
`data/evaluation/external/`; symlinks/outside paths and overwriting existing results are rejected.
The one-line commands are PowerShell syntax; the actual blocked invocation here used Linux.

## Limitations and remaining validation

All nine questions rely on the same dominant specifications PDF; only one procurement family
and three parseable documents are indexed. Three original cases depend on an encrypted PDF
and remain excluded. Upstream QA/reference answers were LLM-curated; declared source spans
do not independently prove every answer/inference. Human semantic checks, rights review and
actual reviewed-artifact availability remain prerequisites. This sample cannot establish general
retrieval accuracy or German procurement quality.

The new deterministic tests use original synthetic text/PDFs and synthetic reviewer metadata
only. They cover complete/partial/wrong-document/page matches, normalization, multiple spans
and documents, duplicate/empty hits, macro aggregation, size/boundary diagnostics, missing
artifacts, tampering and stale COMPLETE reports. No downloads, live embeddings or LLM calls
occur in CI. Real measured scores/misses, structural counts and their interpretation remain
pending execution with the COMPLETE human-reviewed input set.
