# Data models

Pydantic definitions live in `src/tendercite/domain/`; OpenAPI exposes request/response schemas.
SQLite initialization is additive (`CREATE TABLE IF NOT EXISTS`) and reads v0.1 source tables.
Back up existing databases before upgrades; there is not yet a versioned migration framework.

| Entity | Persistence / important fields |
| --- | --- |
| Document | `documents`: UUID, original sanitized filename, MIME, SHA-256, page/chunk counts, UTC timestamp |
| Page | `pages`: document ID + 1-based PDF page sequence, normalized extracted text |
| Chunk | `chunks`: UUID, document ID, page, ordinal, text, char_start/end in stored page text |
| SearchHit | Transient: chunk/document IDs, document name, page, text, cosine similarity |
| AnalysisRun | `analyses`: ID + validated JSON snapshot of provider/model, embedding model, request, retrieved chunk IDs, prompt/schema versions, UTC time, original findings |
| Finding | `findings`: ID, analysis FK, immutable JSON original statement/category/type/confidence/evidence |
| EvidenceRef | Embedded in Finding: document/page/chunk, quote, offsets, normalized-page hash, deterministic grounding status |
| ReviewEvent | `reviews`: ordered sequence, finding FK, immutable JSON original/previous/reviewed values, previous status, action, comment, UTC timestamp |
| Assessment | `assessments`: ordered sequence, finding FK, review event ID, status/rationale JSON, UTC timestamp |
| GoNoGoRow | Derived from active reviews and current assessment; criterion, categories, review/grounding status, source evidence |

Finding `effective_value` and `review_status` are derived from reviews without overwriting original
fields. Analysis snapshots do not change with reviews. JSON exports additionally include the full
review history and resolved document names. CSV/Markdown flatten to one row per evidence reference.

Evidence offsets differ from chunk offsets: evidence offsets refer to the **single-line
whitespace-normalized page representation** (`normalize_quote(page.text)`), not raw PDF bytes
or the stored multiline text. `text_sha256` fingerprints that same normalized representation.
Repeated quotations use the first matching occurrence. Neither offsets nor hashes prove
legal authenticity. Missing evidence has no source offsets; invalid quotes are never verified.

Taxonomy is deliberately small: MUST/SCORING/INFORMATION/RISK plus the existing requirement-type
enum. Review state: UNREVIEWED/CONFIRMED/MODIFIED/REJECTED. Assessment state:
FULFILLED/PARTIAL/MISSING/CLARIFICATION_NEEDED/NOT_APPLICABLE.

`Finding.original_output` additionally preserves the complete parsed candidate, including the
original quote whitespace before deterministic normalization. `embedding_revision` records
the configured revision (null when unpinned); it does not claim a resolved download commit.
