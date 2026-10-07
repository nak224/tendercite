# REST API

The running server publishes complete request/response schemas at `/docs` and `/openapi.json`.
Application paths use `/api/v1`; there is no authentication. Keep the service local/trusted.

| Method | Path | Behavior |
| --- | --- | --- |
| GET | `/health` | Liveness only; does not load models |
| GET | `/api/v1/configuration` | Non-secret model/configuration summary |
| POST | `/api/v1/documents` | Multipart `file`; parse/store/index PDF; returns document (201) |
| GET | `/api/v1/documents` | List documents |
| GET | `/api/v1/documents/{id}` | Document metadata |
| GET | `/api/v1/documents/{id}/pages/{page}` | Stored source text; PDF sequence is 1-based |
| GET | `/api/v1/documents/{id}/chunks` | Page-bounded chunks and exact stored-text offsets |
| DELETE | `/api/v1/documents/{id}` | Remove unreferenced source and vectors (204) |
| POST | `/api/v1/documents/{id}/index` | Idempotent reindex from SQLite |
| POST | `/api/v1/evidence/validate` | Check document/page/quote occurrence |
| POST | `/api/v1/search` | Semantic search with citation metadata |
| POST | `/api/v1/analyses` | Retrieve, extract, validate and atomically persist results (201) |
| GET | `/api/v1/analyses/{id}` | Immutable original run snapshot |
| GET | `/api/v1/findings` | Current findings; optional `analysis_run_id` query filter |
| GET | `/api/v1/findings/{id}` | Original output plus current `effective_value` and review state |
| POST | `/api/v1/findings/{id}/reviews` | Append CONFIRM/MODIFY/REJECT event (201) |
| GET | `/api/v1/findings/{id}/reviews` | Ordered immutable review history |
| GET | `/api/v1/go-no-go` | Confirmed/modified findings; optional `analysis_run_id` filter |
| PATCH | `/api/v1/go-no-go/{finding_id}` | User status/rationale assignment |
| GET | `/api/v1/exports` | `format=json|csv|markdown`; optional `analysis_run_id` filter |

## Examples

Search request:

```json
{"query":"Which references are required?","document_ids":["document-uuid"],"top_k":5}
```

Omitted `document_ids` searches all indexed sources; `[]` searches none. `top_k` is 1–50.
Results carry `chunk_id`, `document_id`, `document_name`, `page_number`, `text`, `score`.
Scores are cosine similarity, not probabilities.

Analysis request:

```json
{"document_ids":["document-uuid"],"query":"Find mandatory reference requirements","top_k":12}
```

Analysis requires a nonempty explicit document selection and a configured provider. Only retrieved
passages are sent. Findings carry category/type/confidence and evidence with server-assigned
VERIFIED_QUOTE/INVALID_QUOTE/MISSING_EVIDENCE. Invalid/missing findings remain visible for review.
A valid quote is not proof of logical entailment or completeness. No bidder facts are inferred.

Review request:

```json
{"action":"MODIFY","reviewed_value":{"statement":"Two comparable references are required","category":"MUST","requirement_type":"REFERENCE"},"comment":"Clarified wording after checking source"}
```

`MODIFY` requires all three reviewed fields. CONFIRM/REJECT omit `reviewed_value` and retain
the latest effective value. Every event preserves the original output, previous/current values,
previous status, action, comment and timestamp. Confirmation does not alter evidence grounding.

Assessment request:

```json
{"status":"PARTIAL","rationale":"Bidder has supplied one of the two required references"}
```

Statuses: FULFILLED/PARTIAL/MISSING/CLARIFICATION_NEEDED/NOT_APPLICABLE. Rationale is required.
Unreviewed/rejected findings are excluded. New rows and rows after any new review default to
CLARIFICATION_NEEDED; old assignments remain stored but do not silently apply to changed reviews.

## Lifecycle and errors

- Identical PDF bytes reuse the same source IDs and vectors (upload still returns 201).
- Source storage and vector indexing are separate. An indexing error returns 503 with the
  source retained; fix model/index availability and retry upload or explicit indexing.
- A missing/disabled retrieval provider returns 503; invalid request data returns 422.
- Upload failures: 413 size limit, 415 format/MIME/header, 422 malformed/encrypted/over-limit PDF.
- Missing records return 404. Referenced source deletion returns 409 to preserve auditability.
- Analysis returns 422 for no usable context or invalid structured response; provider/retrieval
  failures return 502. No partially persisted run is presented as successful.
- Matrix changes require a reviewed active finding (otherwise 409).

JSON exports include original/effective findings, evidence, resolved filenames, review history
and current assessments. CSV/Markdown use one row per evidence reference; missing evidence
still produces a row. Unreviewed/rejected records retain their labels and lack matrix assignments.
CSV neutralizes spreadsheet formula prefixes; Markdown escapes untrusted markup. Exports are
attachments with fixed filenames. There is no upload-by-URL, arbitrary storage-path or secret API.
