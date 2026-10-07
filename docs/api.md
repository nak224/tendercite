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
