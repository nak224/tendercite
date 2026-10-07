# API

Base URL for local development: `http://localhost:8000`.

Interactive OpenAPI documentation is available at `/docs` when the API is running.

## Current v0.1 endpoints

| Method | Path | Purpose |
|---|---|---|
| GET | `/health` | Liveness check. |
| POST | `/api/v1/documents` | Upload and parse one PDF. |
| GET | `/api/v1/documents` | List imported documents. |
| GET | `/api/v1/documents/{document_id}` | Get document metadata. |
| GET | `/api/v1/documents/{document_id}/pages/{page_number}` | Get normalized page text. |
| GET | `/api/v1/documents/{document_id}/chunks` | Get page-bounded chunks. |
| DELETE | `/api/v1/documents/{document_id}` | Delete imported document metadata and source file. |
| POST | `/api/v1/evidence/validate` | Validate a quote against a document page. |

## Planned v0.5/v1.0 endpoints

```text
POST /api/v1/search
POST /api/v1/analyses
GET  /api/v1/analyses/{analysis_id}
GET  /api/v1/findings
GET  /api/v1/findings/{finding_id}
POST /api/v1/findings/{finding_id}/reviews
GET  /api/v1/go-no-go
PATCH /api/v1/go-no-go/{finding_id}
GET  /api/v1/exports?format=json|csv|markdown
```

The exact route shape may evolve before v1.0, but evidence objects remain explicit API data rather than presentation-only citation strings.

## Implemented retrieval

`POST /api/v1/search` accepts `query`, `top_k` (1–50) and optional `document_ids`.
An empty document list searches nothing. Responses include document name, ID, page,
chunk ID, source text and cosine similarity (not a probability).
`POST /api/v1/documents/{id}/index` safely reindexes existing chunks.
Uploads index automatically; identical PDF bytes reuse the existing document and vectors.
On indexing failure the source remains stored; retry upload or the index endpoint.
Install `.[retrieval]` and allow the first E5 model download, or use a prepopulated cache.
`TENDERCITE_RETRIEVAL_ENABLED=false` supports ingestion-only development.

## Grounded analysis

`POST /api/v1/analyses` takes explicit `document_ids`, optional `query` and `top_k`.
Configure `TENDERCITE_LLM_BASE_URL`, `TENDERCITE_LLM_MODEL` and, when needed,
`TENDERCITE_LLM_API_KEY` in the process environment. The provider must support
OpenAI-compatible chat completions with JSON schema output.
Only retrieved chunks from the selected documents are sent to that provider.
Each citation must match a retrieved chunk, its document, page and stored page text.
Invalid/missing evidence remains explicitly marked; quote validity does not establish
that the statement logically follows from the quote. Human review is still required.
`GET /api/v1/analyses/{id}`, `GET /api/v1/findings?analysis_run_id=...` and
`GET /api/v1/findings/{id}` expose persisted results and run metadata.
Source documents referenced by analyses cannot be deleted (409), preserving the audit trail.

## Human review

`POST /api/v1/findings/{id}/reviews` accepts `action` (`CONFIRM`, `MODIFY`, `REJECT`)
and `comment`. `MODIFY` additionally requires `reviewed_value` with `statement`,
`category` and `requirement_type`. Other actions retain the latest effective value.
`GET /api/v1/findings/{id}/reviews` returns append-only history, original output,
previous/current values and timestamps. Finding fields preserve the original AI output;
`effective_value` and `review_status` reflect review history. Analysis responses remain
immutable snapshots. Human confirmation does not change evidence grounding status.

## Decision matrix

`GET /api/v1/go-no-go?analysis_run_id=...` contains confirmed/modified findings only.
`PATCH /api/v1/go-no-go/{finding_id}` takes `status` and a nonempty `rationale`.
Statuses are FULFILLED, PARTIAL, MISSING, CLARIFICATION_NEEDED, NOT_APPLICABLE.
No bidder capability is inferred. New rows default to CLARIFICATION_NEEDED.
Any later review invalidates the effective assignment; reassess after reviewing.
Rejected findings leave the matrix. Previous assignments remain stored for audit.

## Exports

`GET /api/v1/exports?format=json|csv|markdown&analysis_run_id=...` downloads results.
JSON includes original/effective findings, evidence, review history and current assessments.
CSV/Markdown use one row per evidence reference; missing evidence still produces a row.
Unreviewed/rejected findings remain labeled and have no matrix assignment.
CSV neutralizes spreadsheet formula prefixes; Markdown escapes source-controlled markup.
