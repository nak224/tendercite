# Synthetic evaluation

`gold.json` version `synthetic-tender-3` contains 14 manually specified gold requirements:
all six original English and six German cases, plus a dedicated SECURITY case in each language.
SECURITY covers privileged access authentication/logging independently of PRIVACY's data
residency/encryption requirement. Quotes, queries, labels and pages of the existing 12 cases
are retained. No real procurement documents are redistributed; this original dataset and its
PDF generator are Apache-2.0 by the TenderCite authors.

## Corpus and chunking

The eight-page fixture has six requirement-bearing pages of roughly 1,900–2,300 characters,
above the production 1,200-character chunk size (150-character overlap). Each mixes requirements
with administrative background and unrelated procurement vocabulary: historical turnover figures,
obsolete insurance forms, sample price weights, archived deadlines and security training events.
Requirements occur in different chunks of a shared page. Two additional pages are distractors.
The live runner and offline tests use these full pages, with normal chunking settings.

Offline tests assert that actual PDF ingestion creates more chunks than pages, that all six
relevant pages produce multiple chunks, and that every gold quote exists completely in at least
one chunk. They also select a real wrong chunk on each gold page: this must fail the source-span
metric even though it passes the page metric. German umlauts/ß, hyphenated words, distractors and
exact stored-page chunk offsets are checked after parsing. The generator uses Windows-1252 /
WinAnsi with line wrapping that preserves words; it does not provide general Unicode PDF support.

## Running real-model evaluation

The default `intfloat/multilingual-e5-small` enables cross-lingual German/English retrieval;
model choice alone does not establish retrieval quality in either language. Before live
evaluation or v1.0.0, download and verify a concrete embedding-model revision and pin that commit
using `TENDERCITE_EMBEDDING_REVISION`. The tested default is now
`614241f622f53c4eeff9890bdc4f31cfecc418b3`; see the [real results](../docs/evaluation/multilingual-e5-synthetic-tender-3.md). Reindex after
changing models/revisions and retain the revision with the evaluation report. Then run against
an API using the pinned model:

```bash
python -m evaluation.run --output /tmp/retrieval-evaluation.json
# Optional: sends ONLY synthetic retrieved passages to the configured LLM
python -m evaluation.run --analysis --output /tmp/full-evaluation.json
```

The report records actual API outputs, configuration, timestamp and dataset version. Retrieval
probes use each gold case's query through `/search`; these measure query-level retrieval, not
aggregate category-plan coverage. With `--analysis`, the report also includes the actual
category-aware `analysis_run`, its complete retrieval plan/audit metadata and extraction metrics.
The plan uses the user's query plus six fixed bilingual category queries, at most three hits each,
and deduplicated round-robin selection up to the requested context budget (12 by default, cap 21).
See [architecture](../docs/architecture.md#category-aware-analysis-retrieval-ret-1).

## Metric definitions

For the first `k` returned chunks of each gold query:

- `page_hit_at_k`: fraction of cases with at least one chunk from the gold page **and selected
  document**. This is page discovery only; it may count irrelevant text on the correct page.
- `source_span_hit_at_k`: fraction with at least one chunk from that document/page containing
  the **complete whitespace-normalized gold quote**. A quote elsewhere on the page, in another
  document, beyond rank `k`, or split across chunks does not count. Partial spans are misses.
  Matching is case-sensitive and does not infer paraphrase equivalence.

Missing results count as misses; both rates are null for an empty gold dataset. A case counts
at most once regardless of duplicate hits. The old ambiguous `hit_at_k` report key is replaced
by these two explicit keys. Scores from earlier page-only reports are not source-span scores.

Extraction precision/recall/F1 use exact whitespace-normalized quote + document + page +
category + type matches. One prediction can match one gold item; duplicates count against
precision. These are **source-span/type metrics**, not semantic correctness of paraphrased
statements. Partial but valid quotes may score as misses. Evidence verification rate measures
validated references; unsupported finding rate includes findings with no evidence or any
invalid/missing reference. Undefined evidence rates are null, not perfect scores.

## Offline regression and limits

CI uses deterministic fake embeddings and fake/mock LLMs, never Hugging Face model downloads
or paid APIs. Category-plan regressions use real SQLite/Chroma, English and German texts spread
across a selected document package, and an unselected document. Deliberately constructed fake
vectors make a broad query fill its context with generic passages; the plan supplies all six
requirement groups within the same context budget. Other tests cover bounded selection,
deduplication, maximum scores, deterministic ties, persisted metadata and all citation boundaries.
These are controlled behavioral regressions, **not measured multilingual E5 quality scores**.

[RET-1](../docs/roadmap.md#ret-1--analysis-retrieval-coverage) is implemented, but bounded retrieval
can still miss requirements, subtopics or entire documents. TenderCite does not claim exhaustive
extraction. Eight synthetic pages cannot establish real-tender accuracy, table/OCR handling or
robustness to ambiguity. German retrieval quality, real-model category coverage and real LLM
extraction require further release work. Synthetic retrieval and context coverage have now been
measured on the pinned model; live LLM extraction remains unrun.

The [completed real-model evaluation](../docs/evaluation/multilingual-e5-synthetic-tender-3.md)
includes per-language query metrics, category-plan versus broad-query coverage, per-case hits,
performance and reproduction commands using `python -m evaluation.real_retrieval`. It supersedes
the historical blocked attempt. That opt-in runner uses no LLM and is not executed by CI.

## Real-notice acquisition pilot

`python -m evaluation.acquire_ted --help` documents a separate bounded TED Search API/download
workflow. The [pilot report](../docs/evaluation/ted-pilot.md) contains exact commands and the
[candidate manifest](ted-pilot/candidates.json) contains real German notice metadata and download
failures. This is not a gold dataset: 17 metadata candidates remain unannotated, no PDF/XML was
obtained due to TED WAF challenges, and no real-tender scores exist. Normal CI uses only mocked
TED responses and generated PDF/XML fixtures; the live smoke test requires explicit opt-in.

## Manually downloaded procurement PDFs

`python -m evaluation.import_pdfs` registers a directory of local PDFs into an ignored,
hash-addressed corpus, without fetching sources, indexing documents or calling an LLM.
It preserves original names and source/rights metadata, supports multiple PDFs per tender,
deduplicates bytes and records validation failures and incomplete provenance. TED IDs are
optional. See the [manual import workflow](../docs/evaluation/real-pdf-import.md) for exact
commands, per-file metadata, manifest fields and read-only `--verify` checks.

Automated tests use self-generated PDFs only. No genuine documents were obtained through this
workflow yet; real procurement PDFs and human provenance/rights review are required before a
corpus can support subsequent manual annotation and real-tender retrieval evaluation.

## Human annotation and gold export

The [annotation guide](../docs/evaluation/real-tender-annotations.md) contains the complete
synthetic example, manual review steps and CLI commands. [The versioned JSON Schema](annotation-schema.json)
defines human-authored source quotes, requirement statements/categories/types, aliases and review
states. `python -m evaluation.validate_annotations` validates each entry against the local corpus
and optionally exports verified gold with hashes, exclusions and deterministic tender-level splits.
Exact quote occurrence does not validate the interpretation; distinct reviewer aliases are human
declarations. No genuine documents have been annotated and no new model evaluation is performed.

## External procurement QA pilot

`python -m evaluation.acquire_external_benchmark` optionally acquires the public EU Tenders QA
metadata and PDFs for one selected family; `--validate-only` checks local hashes, schema and
document references without networking. See the [actual pilot report](../docs/evaluation/external-benchmark-pilot.md)
and [small machine-readable summary](../docs/evaluation/external-benchmark-pilot.json).
The 12-case selected family has nine usable document-reference cases and an encrypted invitation.
No original chunk/page/span mapping or independent human-gold evidence is available. Artifacts
remain under ignored `data/evaluation/external/`; no answers or third-party PDFs are committed.
Normal CI uses original synthetic fixtures and mocked SDK/HTTP behavior, never live downloads.

## Exploratory external document retrieval

`python -m evaluation.external_document_retrieval --run-real-model` is an opt-in real-model runner.
It requires the pinned acquisition directory, a fresh output directory and an ignored model cache.
It rechecks artifact hashes/parsing, excludes complete cases with unavailable referenced PDFs,
indexes all parseable family PDFs together with existing services, and ranks unique documents
from a bounded top-50 chunk pool. The [report](../docs/evaluation/external-document-retrieval.md)
contains actual per-case/aggregate document metrics, exclusions, model verification, label bias
and one-line PowerShell commands. Questions, retrieved chunk text and model outputs remain in
ignored local `results.json`. No source-span scores, verified gold or LLM evaluation are produced.
