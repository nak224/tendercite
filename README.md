# TenderCite

**Evidence-grounded AI for public procurement documents**

TenderCite turns text-based tender PDFs into source-linked findings that a human can review
and use in a Go / No-Go matrix. Every claimed citation is checked against a stored document,
page and text passage. A valid quotation does **not** prove the model's interpretation is correct.

**Status:** unreleased development work built on v0.1. The complete workflow is implemented
and tested with deterministic model doubles. The first real E5 synthetic retrieval evaluation is
measured; live LLM evaluation and release review remain open. This repository is **not yet
declared v1.0.0**. See [release checks](docs/release-check.md).

## What works

- Multi-PDF upload, page-aware pypdf parsing, bounded chunks and exact source offsets.
- SQLite persistence and duplicate-upload detection by SHA-256.
- Replaceable local embeddings (default `intfloat/multilingual-e5-small`) and persistent Chroma search.
- Document-scoped retrieval with source metadata; idempotent reindexing.
- Explicit bilingual analysis query plan, bounded category context and retrieval audit metadata.
- OpenAI-compatible structured extraction with Pydantic validation and per-citation checks.
- Explicit VERIFIED_QUOTE / INVALID_QUOTE / MISSING_EVIDENCE states.
- Confirm, modify or reject findings while preserving original AI output and review history.
- Human-managed Go / No-Go statuses, defaulting to CLARIFICATION_NEEDED.
- JSON, CSV and Markdown exports; Streamlit UI; FastAPI/OpenAPI.
- Synthetic evaluation corpus, offline tests, hashed dependency locks, Docker and CI definitions.

## Architecture

```mermaid
flowchart TD
    PDF[PDF upload] --> Parser[Page-aware parsing]
    Parser --> SQLite[(SQLite source pages)]
    SQLite --> Chunks[Page-bounded chunks]
    Chunks --> Embeddings[EmbeddingProvider: local E5]
    Embeddings --> Chroma[(Chroma local index)]
    Plan[User query + six fixed bilingual category queries] --> Retrieval
    Chroma --> Retrieval[Bounded retrieval + deduplication + round-robin selection]
    Retrieval --> LLM[StructuredLLM: OpenAI-compatible adapter]
    LLM --> Validation[Schema and deterministic evidence validation]
    SQLite --> Validation
    Validation --> Findings[Original findings]
    Findings --> Review[Human review and audit trail]
    Review --> Matrix[Human-managed Go / No-Go matrix]
    Matrix --> Export[JSON / CSV / Markdown]
    UI[Streamlit] --> API[FastAPI modular monolith]
    API --> PDF
    API --> Retrieval
    API --> Review
```

No agent framework, microservices or remote Chroma server is required. See
[architecture](docs/architecture.md) and [data models](docs/data-models.md).

## Quick start (Python 3.12, Linux CPU)

```bash
git clone https://github.com/nak224/tendercite.git
cd tendercite
python -m pip install uv==0.12.19
uv venv .venv
uv pip install --python .venv/bin/python --torch-backend cpu --require-hashes -r requirements-dev.lock
uv pip install --python .venv/bin/python --no-deps -e .
source .venv/bin/activate
uvicorn tendercite.main:app --host 127.0.0.1 --port 8000
# In a second terminal, activate the same environment:
streamlit run frontend/app.py --server.address=127.0.0.1
```

The first text upload/search downloads E5 model files from Hugging Face. The tested default
`TENDERCITE_EMBEDDING_REVISION` is `614241f622f53c4eeff9890bdc4f31cfecc418b3`;
it remains configurable. Reindex after changing model or revision. Allow sufficient
network access and a writable Hugging Face cache; set `HF_HOME` if necessary. If unavailable,
the source remains stored and upload returns 503: fix model access and retry upload or
`POST /api/v1/documents/{id}/index`. To work on ingestion alone, explicitly set
`TENDERCITE_RETRIEVAL_ENABLED=false` before starting the API.

For other platforms, install `.[dev,retrieval,ui]` using your platform's PyTorch instructions;
the checked-in locks target Python 3.12 Linux CPU. Revalidate on other platforms.

### Configure structured analysis

```bash
export TENDERCITE_LLM_BASE_URL=http://localhost:11434/v1
export TENDERCITE_LLM_MODEL=your-served-model
# Set TENDERCITE_LLM_API_KEY securely if your provider requires it.
```

Restart the API after changing settings. The server must support OpenAI-compatible
`/chat/completions` and JSON-schema output; compatibility with every Ollama/vLLM/LM Studio
version is not claimed. No model server is bundled. The API reads `.env`; the Streamlit
API URL must be exported as `TENDERCITE_API_URL` when overriding its default.

Analysis sends selected retrieved passages to the configured provider. Review its privacy,
retention and model-license terms first. Offline tests do not need model downloads or paid APIs.

### UI walkthrough

1. Import PDFs in **Documents**; inspect page/chunk counts.
2. Use **Search** and select source documents in the sidebar.
3. In **Analysis & review**, choose a focus and explicitly permit sending passages.
4. Inspect quotes and source pages; confirm, modify or reject each finding with a comment.
5. In **Go / No-Go**, record bidder facts, status and rationale.
6. Download **Export** results. Unreviewed/rejected findings remain explicitly labeled.

## Tests and evaluation

```bash
pytest
ruff check .
ruff format --check .
# With a working real embedding model and running API:
python -m evaluation.run --output /tmp/retrieval-evaluation.json
# Additionally uses the configured LLM, with synthetic text only:
python -m evaluation.run --analysis --output /tmp/full-evaluation.json
```

Tests exercise real SQLite/Chroma and the API/UI with deterministic embedding/LLM doubles.
The [14-case English/German evaluation](evaluation/README.md) uses multi-chunk pages with
procurement distractors and separate SECURITY/PRIVACY cases. It distinguishes `page_hit_at_k`
from `source_span_hit_at_k` (the retrieved chunk must contain the gold quote), alongside exact
extraction source-span/type precision/recall/F1 and evidence rates. Offline regressions test
category coverage and citation boundaries; they do not establish real-model retrieval quality
or broad tender-analysis accuracy.

The [real E5 evaluation](docs/evaluation/multilingual-e5-synthetic-tender-3.md) measured
source-span Hit@1 of 6/7 and Hit@3/@5 of 7/7 in each language. At a 12-chunk budget, the category
plan covered 11/14 requirements (11 unique chunks), versus 12/14 for the broad-query baseline.
The report includes the misses, raw results, CPU timings and reproduction steps. These 14
synthetic cases do not establish real-tender accuracy or exhaustive extraction.

The [TED evaluation acquisition pilot](docs/evaluation/ted-pilot.md) adds a bounded official
Search API client and `python -m evaluation.acquire_ted` CLI. Live search obtained 20 German
competition-notice metadata records, with 17 provisional candidates. PDF/XML downloads were
blocked by TED's WAF; parsing and the suitable-document shortlist remain pending. This is
evaluation preparation for [issue #4](https://github.com/nak224/tendercite/issues/4), with no
real-tender scores or automatic discovery/analysis claim. Downloaded files stay outside Git.

For permitted manual downloads, the evaluation-only
[`python -m evaluation.import_pdfs` workflow](docs/evaluation/real-pdf-import.md) registers local
PDFs with unchanged bytes, SHA-256 deduplication, tender grouping and a provenance/validation
manifest. Missing or unverified source and rights information stays explicitly flagged. Tests use
self-generated PDFs; genuine documents, rights review and manual annotation remain pending.

## Docker

```bash
docker compose up --build
```

Both services run as a non-root user. Host ports bind to loopback; SQLite, source PDFs,
Chroma and downloaded models live in the `tendercite-data` volume. Processes restart;
persistent files remain. See [deployment](docs/deployment.md) for configuration and proxy CAs.

## API

OpenAPI is served at `/docs`. Core routes are under `/api/v1`: `documents`, `search`,
`analyses`, `findings`, `findings/{id}/reviews`, `go-no-go`, `exports`. Liveness: `/health`.
Examples and error semantics: [API documentation](docs/api.md).

## Limitations and responsible use

- Not legal or procurement advice. Findings and bidder assessments require human review.
- Confidence is an uncalibrated model signal, not a probability of correctness.
- Quote validation proves text occurrence and source identity, not logical entailment.
- Analysis uses six fixed bilingual category queries plus the user query, with three hits per
  query and deduplicated round-robin context selection (default 12, at most 21 chunks).
  Bounded retrieval can still miss requirements in long or multi-document packages; extraction
  is not exhaustive. See [RET-1](docs/roadmap.md#ret-1--analysis-retrieval-coverage).
- No OCR; scans, complex layouts/tables and encrypted PDFs are not reliably supported.
- German/English retrieval is measured only on seven synthetic cases per language. The category
  plan missed three German requirements; representative real-tender evaluation remains necessary.
- No authentication, multi-tenancy, bidder capability inference or legal automation.
- Local documents are confidential data. Use a trusted single-user deployment; see
  [SECURITY.md](SECURITY.md) and the [dependency audit](docs/security-review.md).
- Sources referenced by analyses are retained for audit; no selective history purge UI exists.

## License and handover

TenderCite code and its explicitly identified synthetic evaluation fixture use Apache-2.0.
This does not license third-party model weights, dependencies or uploaded documents.
The default multilingual E5 model is separately MIT-licensed; review and pin a concrete revision
before live evaluation or v1.0.0, and verify its license before redistributing weights.
See [third-party notices](THIRD_PARTY_NOTICES.md), [contribution guide](CONTRIBUTING.md),
[changelog](CHANGELOG.md) and [remaining roadmap](docs/roadmap.md).
