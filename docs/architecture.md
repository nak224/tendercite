# Architecture

## Design goals

TenderCite is an evidence-grounded document-intelligence application for public procurement documents. The central design requirement is that an extracted finding can be traced back to a specific source document, page and quoted passage. AI output is reviewable data, not an autonomous procurement decision.

## Architectural style

TenderCite uses a **modular monolith** for v1.0. This keeps deployment simple while preserving boundaries that can later be split if needed.

```text
Browser / Streamlit
       |
       v
    FastAPI
       |
       +-- document ingestion ----> PDF parser
       |                              |
       |                              v
       |                         page records
       |                              |
       |                              v
       |                         page-bounded chunks
       |
       +-- retrieval -----------> embeddings -> Chroma
       |
       +-- extraction ----------> StructuredLLM adapter
       |                              |
       |                              v
       |                         Pydantic validation
       |                              |
       |                              v
       +-- evidence validator <--- quoted source spans
       |
       +-- review workflow ------> SQLite
       |
       +-- Go/No-Go view --------> reviewed findings
       |
       +-- export ---------------> JSON / CSV / Markdown
```

## Module boundaries

| Module | Responsibility |
|---|---|
| `services/pdf_parser.py` | Parse PDF pages and preserve page identity. |
| `services/chunking.py` | Create retrieval chunks that never cross a page boundary. |
| `services/retrieval/` | Embedding and vector-store abstractions. |
| `services/llm/` | Provider-neutral structured generation interface. |
| `services/evidence.py` | Verify source quotes against stored page text. |
| `repositories/` | Persistence for documents, pages, chunks, findings and reviews. |
| `api/routes/` | Stable REST surface. |
| `frontend/` | Thin review UI; business logic stays in the API/domain layer. |

## Concrete v1 stack

- Python 3.12
- FastAPI + Pydantic v2
- pypdf as the default MVP PDF parser for digitally generated PDFs
- optional Docling adapter after the vertical slice for harder layouts/tables
- sentence-transformers with `intfloat/e5-small-v2` as the initial local embedding model
- Chroma in embedded/local mode as the vector store
- SQLite for application state, review history and metadata
- a small `StructuredLLM` protocol plus an OpenAI-compatible HTTP adapter rather than a large orchestration framework
- Streamlit as a thin UI for v1.0
- pytest + Ruff + GitHub Actions
- Docker / Docker Compose

## Why not a large RAG framework?

TenderCite intentionally keeps retrieval, extraction and citation validation as explicit application code. This makes the provenance path inspectable, keeps provider replacement straightforward and avoids hiding core behavior behind framework-specific abstractions.

## Evidence and citation invariant

The evidence model is the most important architectural constraint.

1. A PDF is parsed page by page.
2. Page numbers are stored as first-class metadata.
3. Retrieval chunks are generated **within a single page only**.
4. Retrieval returns chunk IDs together with document ID and page number.
5. The extraction prompt receives retrieved chunks with stable source IDs.
6. An extracted finding must return one or more evidence quotes and source IDs.
7. The API validates each quote against the stored text of the declared page.
8. A quote that cannot be found is marked `INVALID_QUOTE`; missing evidence is marked `MISSING_EVIDENCE`.
9. Only `VERIFIED_QUOTE` evidence is rendered as a verified citation.
10. Manual edits create a new reviewed value while preserving the original machine output.

This makes citation correctness partly deterministic rather than relying on model self-reporting.

### Why page-bounded chunks?

A chunk that spans two pages makes a citation ambiguous. Page-bounded chunks trade a small amount of retrieval context for a much stronger provenance guarantee. Neighboring page chunks can still be retrieved independently when additional context is needed.

## Human-review state model

A finding begins as `UNREVIEWED` and may transition to:

- `CONFIRMED`: reviewer accepts the extracted statement and evidence.
- `MODIFIED`: reviewer changes the statement/classification; original AI output remains stored.
- `REJECTED`: reviewer marks the finding unusable or incorrect.

Every review event should later store timestamp, reviewer identifier (when authentication exists), comment and before/after values. v1.0 does not require multi-user authentication; the data model should not block adding it later.

## Go/No-Go safety rule

TenderCite does not infer that a bidder fulfills a requirement merely because the requirement was extracted. Matrix rows default to `CLARIFICATION_NEEDED` until a human (or a future explicitly supplied capability profile) provides the bidder-side fact needed for assessment.

## Security and privacy

- Treat PDF content as untrusted data.
- Never execute embedded PDF content.
- Enforce upload size and file-type checks.
- Store secrets only in environment variables.
- Keep uploaded documents under a git-ignored data directory.
- Make remote-vs-local LLM usage visible in configuration/UI.
- Do not send documents to a remote provider unless the user has configured that provider.
- Prompts must state that document text is content to analyze, not instructions to follow.

## Future parser path

pypdf is deliberately used first because it is lightweight and reliable for ordinary text PDFs. Docling is the first advanced-parser candidate for tables, layout and OCR-oriented workflows. The parser interface isolates this choice so the MVP is not blocked by heavyweight document-processing dependencies.
