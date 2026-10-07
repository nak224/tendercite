# Architecture

TenderCite is a modular monolith. FastAPI coordinates explicit application services;
Streamlit is an HTTP client. SQLite owns source content and application state. Chroma is
an embedded, replaceable retrieval index. There is no Chroma HTTP server or agent framework.

## Modules

| Module | Responsibility |
| --- | --- |
| `api/routes/documents.py` | Upload validation, source lifecycle, ingestion orchestration |
| `services/pdf_parser.py` | Bounded page-aware pypdf extraction |
| `services/chunking.py` | Overlapping, page-bounded chunks and stored-page offsets |
| `services/retrieval/` | EmbeddingProvider, VectorStore, E5/Chroma adapters, search service |
| `services/llm/` | StructuredLLM protocol and OpenAI-compatible HTTP adapter |
| `services/analysis.py` | Retrieval, prompt/schema versions, evidence validation and persistence |
| `services/evidence.py` | Deterministic whitespace-normalized source quote matching |
| `repositories/sqlite.py` | Transactional documents, analyses, findings, reviews, assessments |
| `services/export.py` | Structured and safely escaped portable exports |
| `frontend/app.py` | Upload/search/review/matrix/export UI; no persistence logic |
| `evaluation/` | Synthetic gold corpus, PDF generator, metrics and live API runner |

## Source and retrieval invariants

PDFs receive generated storage names. SHA-256 identifies byte-identical uploads; a serialized
SQLite write rechecks duplicates. Page numbering is 1-based PDF page sequence, not printed
page labels. Chunks never span pages. Chunk offsets identify exact slices of stored page text.
E5 receives `passage: ` and `query: ` prefixes, normalized vectors and CPU execution.
Collection identity includes model/revision configuration. Changing models requires reindexing.
Upserts use stable chunk IDs; reindexing does not duplicate vectors. Search returns cosine
similarity, not a calibrated probability. SQLite rehydrates hits, excluding deleted sources.

SQLite and Chroma do not share a transaction. On indexing failure, the source is retained
and the API returns 503; retry upload or the reindex endpoint. There is no automatic background
job queue. A single API worker is the supported embedded-store deployment.

## Evidence and extraction

The model receives only selected retrieved chunks. A candidate citation must reference a
retrieved chunk, agree with its document/page, and quote text in that chunk and stored page.
All evidence items are validated; a mixed valid/invalid finding is aggregate INVALID_QUOTE.
Missing/blank evidence remains MISSING_EVIDENCE. Only quote matching is deterministic:
statement interpretation, classification and completeness still require human review.
Prompts label source documents as untrusted data. Model output is schema checked, bounded
and never executed. Provider failures do not persist partial analysis runs.

Analysis/finding insertion is atomic and rechecks that source documents still exist.
Analysis snapshots preserve original output, retrieved IDs, model identifiers, request,
prompt/schema version and time, never API keys. Model outputs are not guaranteed reproducible
bit-for-bit, even at temperature zero. Pin model revisions and preserve live evaluation reports.

## Reviews and decisions

Finding statement/category/type always remain the original AI output. `effective_value` is
derived from the latest append-only review event. Events preserve original and previous values,
action, comment and timestamp. Analysis GET returns the immutable initial snapshot; findings
GET reflects current review state. Confirmation does not change grounding status.

Only CONFIRMED/MODIFIED findings enter the matrix. Defaults are CLARIFICATION_NEEDED.
User assignments retain rationale and the review event they assess. Any subsequent review
makes an old assignment ineffective until reassessed; rejected findings leave the matrix.
There is no automatic inference about bidder capabilities. Assessments remain stored for audit.

## Operational limits

No authentication or tamper-proof audit trail; use one trusted local operator. Sources referenced
by analyses cannot be deleted through the API. Back up/erase the full data directory deliberately.
No OCR, distributed transactions, queue, background model server or multi-user conflict UI.
Current evaluation does not establish German-language or real-procurement accuracy.
