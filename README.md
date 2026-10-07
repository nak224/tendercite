# TenderCite

**Evidence-grounded AI for public procurement documents.**

TenderCite is an open-source document-intelligence application for analyzing public tender documents with traceable evidence. Its core rule is simple: important findings should point back to the source document, page and supporting text instead of appearing as unsupported AI answers.

> Status: **v0.1 vertical slice** — page-aware PDF ingestion, deterministic chunking, source-quote validation, REST API, local persistence and a minimal upload UI are implemented. Semantic retrieval, structured extraction and the human-review workflow are the next milestones.

## Why TenderCite?

Typical "chat with your PDF" systems optimize for fluent answers. TenderCite optimizes for **reviewability**:

- multiple tender documents can be ingested and kept source-separated;
- chunks never cross page boundaries;
- extracted evidence is modeled as structured data;
- quoted evidence is validated against stored page text;
- unsupported quotes can be rejected deterministically;
- AI findings are designed for human confirmation, modification or rejection;
- Go/No-Go assessment is human-assisted rather than presented as legal advice.

## Target v1.0 capabilities

- Multi-PDF import with page-preserving text extraction
- Semantic search with source-backed hits
- Structured requirement extraction
- Requirement classes: `MUST`, `SCORING`, `INFORMATION`, `RISK`
- Evidence objects containing document, page, quote and grounding status
- Human review with an audit trail
- Go/No-Go matrix
- JSON, CSV and Markdown export
- REST API and a lightweight web UI
- Dockerized local deployment and CI

## Quick start

### API

```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\\Scripts\\activate
pip install -e ".[dev]"
uvicorn tendercite.main:app --reload
```

Then open `http://localhost:8000/docs`.

### Docker Compose

```bash
docker compose up --build
```

This starts:

- FastAPI at `http://localhost:8000`
- Streamlit at `http://localhost:8501`

## Example: validate source evidence

```bash
curl -X POST http://localhost:8000/api/v1/evidence/validate \
  -H "Content-Type: application/json" \
  -d '{
    "document_id": "<document-id>",
    "page_number": 4,
    "quote": "Mindestens zwei vergleichbare Referenzprojekte"
  }'
```

A verified response contains `"grounding_status": "VERIFIED_QUOTE"`. A quote that is not present on that page is returned as `INVALID_QUOTE` rather than silently accepted.

## Architecture

TenderCite is intentionally a modular monolith for v1.0:

```text
PDF -> page parser -> page-bounded chunks -> embeddings/vector search
                                      |                |
                                      |                v
                                      +--------> structured extraction
                                                       |
                                                       v
                                               evidence validation
                                                       |
                                                       v
                                             human review / matrix
```

See [`docs/architecture.md`](docs/architecture.md) for the design rationale and provenance invariant.

## Planned stack

| Layer | Choice |
|---|---|
| Language | Python 3.12 |
| API | FastAPI + Pydantic v2 |
| PDF MVP | pypdf |
| Advanced parser candidate | Docling adapter |
| Embeddings | sentence-transformers / `intfloat/e5-small-v2` |
| Vector store | Chroma, local mode |
| App persistence | SQLite |
| LLM abstraction | Small `StructuredLLM` protocol + OpenAI-compatible adapter |
| UI | Streamlit |
| Quality | pytest, Ruff, GitHub Actions |
| Packaging/deployment | Docker / Docker Compose |

The project does not require a hosted LLM for document ingestion, page tracking or evidence validation.

## Roadmap to v1.0

### v0.1 — provenance foundation

- [x] Professional repository structure
- [x] Page-aware PDF ingestion
- [x] Page-bounded chunking
- [x] SQLite persistence for documents/pages/chunks
- [x] Evidence quote validator
- [x] REST API baseline
- [x] Minimal UI
- [x] Tests and CI configuration

### v0.5 — useful tender analysis

- [ ] Local embeddings + Chroma indexing
- [ ] Semantic search endpoint/UI
- [ ] Structured extraction schema and prompt
- [ ] Extraction of deadlines, eligibility, references, financial, insurance, technical, award, contract, privacy and security requirements
- [ ] Evidence validation for every generated finding
- [ ] Finding list/detail UI

### v1.0 — reviewable decision workflow

- [ ] Confirm / modify / reject workflow with preserved original AI output
- [ ] Review comments and history
- [ ] Human-assisted Go/No-Go matrix
- [ ] JSON / CSV / Markdown export
- [ ] End-to-end fixture and evaluation examples
- [ ] Deployment documentation and demo screenshots
- [ ] License audit notes for dependencies/models/example data
- [ ] Tagged `v1.0.0` release

## Non-goals

TenderCite is not intended to:

- fabricate requirements when evidence is missing;
- make autonomous legal eligibility decisions;
- replace procurement or legal review;
- hide uncertainty behind a single confidence number;
- lock retrieval and citation logic to one commercial LLM provider.

## License

TenderCite source code is licensed under the Apache License 2.0. Third-party dependencies, model weights and datasets keep their own licenses. See [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md).
