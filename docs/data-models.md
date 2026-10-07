# Data models

TenderCite separates source material, machine findings and human decisions so provenance remains inspectable.

## Document

Represents one uploaded PDF.

```text
id
filename
media_type
sha256
page_count
chunk_count
created_at
```

`sha256` supports duplicate detection and auditability; it is not a signature of legal authenticity.

## Page

```text
document_id
page_number   # 1-based, user-visible page sequence in the parsed PDF
text          # normalized extracted text
```

The page is the fundamental citation boundary in v1.0.

## Chunk

```text
id
document_id
page_number
ordinal
text
char_start
char_end
```

Chunks never span multiple pages. `char_start`/`char_end` refer to the normalized stored page text used for retrieval preparation.

## SearchHit

```text
chunk_id
document_id
page_number
text
score
```

The retrieval layer returns source metadata together with text. It never returns anonymous context strings.

## Finding

```text
id
statement
category          # MUST | SCORING | INFORMATION | RISK
requirement_type  # DEADLINE | REFERENCE | FINANCIAL | ...
evidence[]
confidence?       # optional model signal; not treated as calibrated probability
confidence_reason?
review_status
created_at
```

A finding is not considered source-backed merely because it contains a citation-looking string. Its `EvidenceRef` objects must pass server-side validation.

## EvidenceRef

```text
document_id
page_number
quote
char_start?
char_end?
text_sha256?
grounding_status  # VERIFIED_QUOTE | INVALID_QUOTE | MISSING_EVIDENCE
```

`text_sha256` fingerprints the normalized page representation used during validation. It helps detect later source-text changes.

## ReviewEvent (v0.5/v1.0)

```text
id
finding_id
action            # CONFIRM | MODIFY | REJECT
comment?
original_value
reviewed_value?
reviewer_id?
created_at
```

Review events are append-only. The current reviewed finding can be derived without deleting the original model output.

## AnalysisRun (v0.5)

```text
id
document_ids[]
retrieval_config
embedding_model
llm_provider
llm_model
prompt_version
started_at
completed_at?
status
```

This model makes analyses reproducible enough to explain which configured components created a finding.

## GoNoGoRow

```text
finding_id
criterion
status      # FULFILLED | PARTIAL | MISSING | CLARIFICATION_NEEDED | NOT_APPLICABLE
rationale?
evidence[]
```

Rows default to `CLARIFICATION_NEEDED` unless bidder-side facts have been supplied or a human reviewer explicitly sets the status.
