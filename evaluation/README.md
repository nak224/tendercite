# Synthetic evaluation

`gold.json` is an original, manually specified six-page English tender fixture covering
submission deadlines, references, insurance, certificates, price weighting and privacy/security.
The dataset and generator are explicitly licensed under Apache-2.0 by the TenderCite authors.
There are no redistributed third-party procurement documents.

Run against a working API with E5 installed:

```bash
python -m evaluation.run --output /tmp/retrieval-evaluation.json
# Optional: sends ONLY synthetic retrieved passages to the configured LLM
python -m evaluation.run --analysis --output /tmp/full-evaluation.json
```

The report records actual API outputs, configuration, run metadata, timestamp and dataset version.
Retrieval Hit@k checks the expected source page within the selected synthetic document.
Extraction precision/recall/F1 use exact whitespace-normalized quote + page + category + type
matches. A prediction can match one gold item; duplicates count against precision. These are
**source-span/type metrics**, not semantic correctness of paraphrased statements. Partial but
valid quotes may score as misses. Evidence verification rate measures validated references;
unsupported finding rate includes findings with no evidence or any invalid/missing reference.
Undefined evidence rates are null, not perfect scores.

Offline tests verify metric arithmetic, parsing and source grounding; they do not establish E5
or LLM accuracy. No real-model score is claimed until a live report is generated. Six simple
synthetic pages are insufficient for generalization to real tenders, tables, OCR, ambiguity,
prompt injection or German-language documents. E5-small-v2 is primarily an English model;
multilingual quality must be evaluated before claiming German procurement support.

Current onboarding validation: E5 download blocked by network policy (HTTP 403); no LLM
provider configured. Therefore live retrieval/extraction evaluation remains an open release gate.
