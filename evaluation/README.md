# Synthetic evaluation

`gold.json` version `synthetic-tender-2` contains 12 manually specified gold requirements:
the six original English examples plus six German procurement examples covering Angebotsfrist,
Referenzanforderungen, Haftpflichtversicherung, Zertifikat, Zuschlags-/Preisgewichtung and
Datenschutz/IT security. English quotes, queries and labels are retained; their page locations
change in this dataset version.

The separate `pages` array contains the actual eight-page fixture. Requirements share pages
with other requirements and irrelevant administrative text; two pages contain only historical
or descriptive distractors, including similar vocabulary and irrelevant dates/percentages.
The PDF generator uses Windows-1252/WinAnsi encoding and line wrapping to preserve German
umlauts/ß in readable source pages. This is a synthetic fixture, not general Unicode PDF support.
The live runner and offline tests use these full pages, not isolated gold quotes as documents.

The dataset and generator are explicitly Apache-2.0 by the TenderCite authors. No third-party
procurement documents are redistributed. The new model choice, `intfloat/multilingual-e5-small`,
enables multilingual support; German retrieval quality still requires evaluation.

Before live evaluation or v1.0.0, download and verify a concrete embedding-model revision and
pin that commit using `TENDERCITE_EMBEDDING_REVISION`. No revision has been tested or pinned here.
Reindex after changing models/revisions and retain the revision with the evaluation report.
Then run against an API using the pinned model:

```bash
python -m evaluation.run --output /tmp/retrieval-evaluation.json
# Optional: sends ONLY synthetic retrieved passages to the configured LLM
python -m evaluation.run --analysis --output /tmp/full-evaluation.json
```

The report records actual API outputs, configuration, run metadata, timestamp and dataset version.
Retrieval Hit@k checks the expected source page within the selected synthetic document. Because
multiple requirements share pages, a page hit alone is not proof of correct requirement retrieval.
Extraction precision/recall/F1 use exact whitespace-normalized quote + page + category + type
matches. A prediction can match one gold item; duplicates count against precision. These are
**source-span/type metrics**, not semantic correctness of paraphrased statements. Partial but
valid quotes may score as misses. Evidence verification rate measures validated references;
unsupported finding rate includes findings with no evidence or any invalid/missing reference.
Undefined evidence rates are null, not perfect scores.

Offline tests check corpus coverage, real PDF parsing, German text preservation, source grounding
and metric arithmetic. They require no model download or credentials and produce no model scores.
No German, English or aggregate retrieval-quality claim follows from passing these tests.
Eight synthetic pages are insufficient to generalize to real tenders, tables, OCR or ambiguity.

Analysis still uses a single broad query with top-k retrieval; it is not guaranteed to find all
requirements in long or multi-document packages. [RET-1](../docs/roadmap.md#ret-1--analysis-retrieval-coverage)
tracks the intended improvement: deterministic category-specific queries followed by chunk
deduplication. That pipeline change is not implemented in this follow-up.

The prior onboarding download was blocked by network policy (HTTP 403), and no real LLM provider
was configured. The multilingual model has not been downloaded or measured in this follow-up;
real retrieval/extraction evaluation remains a release gate. No evaluation scores are fabricated.
