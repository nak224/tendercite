# Local manual requirement-to-skill mapping API (UI-1b.5a)

These **development-only**, opt-in endpoints expose TenderCite's existing review-bound
mapping contracts and transaction-safe draft/approval lifecycle. They do **not** implement
a second matching algorithm, import workforce data, run AI, select a team, or prove eligibility.
The API milestone is now consumed by the separate [UI-1b.5b manual mapping view](manual-skill-mapping-ui.md).

## Local synthetic setup

Use an existing, disposable TenderCite SQLite database containing synthetic analyses
and an already-imported schema 1.0 capability snapshot. Do not point to the BfS pilot or
any private/real personnel database. The feature is disabled by default:

    TENDERCITE_CAPABILITY_MAPPING_API_ENABLED=false

For a synthetic local backend, explicitly set the flag to true and restart FastAPI.
The backend uses only its configured TENDERCITE_DATA_DIR; no request chooses a database,
file path, URL, or external service. This flag is **not authentication** or a guarantee
that the database actually contains synthetic records. Keep the backend private.

## HTTP contracts

All routes are under /api/v1, carry Cache-Control: no-store, and use fixed privacy-safe
errors of the shape {"detail":{"code":"..."}}.

| Method | Route | Request | Response |
| --- | --- | --- | --- |
| GET | /capability-snapshots | optional limit=1..100 (default 50) | {"status":"LISTED","snapshots":[CapabilitySnapshotMetadata...]} |
| GET | /capability-workflow/overview | required analysis_id, snapshot_hash query | Existing AssessmentOverview contract |
| POST | /capability-mappings/drafts | Existing MappingSelection JSON | Existing CapabilityMappingRecord (201, DRAFT) |
| POST | /capability-mappings/{mapping_id}/approve | Existing MappingApprovalInput JSON | Existing CapabilityMappingRecord (200, APPROVED) |

Snapshot metadata contains content_hash, schema_version, source_system,
assessment_period_start, assessment_period_end and imported_at. No workforce member,
skill assignment or capacity records are exposed in listing responses. The overview
includes the imported skill **catalog labels and UUIDs** by design (synthetic only),
source quotations and reviewed requirement text; it includes no individual workforce
records, personal identifiers, individual assignments or capacity values. Treat even
catalog labels and requirement text as private if non-synthetic data is entered.

The overview is scoped to one explicit analysis_id and snapshot_hash, reuses
assessment_overview and one consistent repository read transaction, and does not
write data. It exposes mapping_preparation_eligible, review binding/digest and mapping
status/revision/skills to support the next React milestone.

### Draft request

The request is the **existing** versioned MappingSelection contract:

```json
{
  "schema_version": "capability-workflow-mapping-selection-1",
  "analysis_id": "analysis-1",
  "finding_id": "finding-1",
  "snapshot_hash": "sha256:FULL_64_CHARACTER_LOWERCASE_HEX",
  "operator": "ALL_OF",
  "skill_ids": ["00000000-0000-0000-0000-000000000065"],
  "rationale": "Human-selected synthetic PostgreSQL skill",
  "operator_alias": "operator_a"
}
```

Replace the illustrative hash with the exact imported snapshot content_hash; the
example placeholder is not a valid request. Skill UUIDs must occur in that snapshot's
catalog. The operator selects at least one skill. The API invokes
prepare_mapping_draft then create_capability_mapping_draft: preparation reads the
current binding and the create transaction rechecks it. A changed review or duplicate
mapping fails without a write. **Draft creation does not imply approval.**

### Approval request

POST /capability-mappings/{mapping_id}/approve takes the *entire existing*
MappingApprovalInput including the same mapping_id as the URL, exact expected_revision,
expected_review_event_id, expected_review_digest, a nonblank rationale and optional
operator_alias. Obtain revision and binding from the freshly loaded overview or the
DRAFT response; do not invent them. The existing repository performs optimistic
revision, current-review and grounding checks in its write transaction. APPROVED is an
explicit operator declaration, not independent proof of skill relevance or qualification.

No automatic retry is implemented. A lost mutation response is not proof that nothing
was saved: reload the overview before explicitly choosing what to do next. Concurrent
draft creation returns one success and a conflict for the duplicates. Existing revisions
are append-only. No silent stale reapproval and no modification of assessments.

## Security and failure semantics

- POST requests use JSON objects, a hard **65,536-byte** bound including requests with no
  Content-Length, strict versioned Pydantic contracts and duplicate-key/nonfinite rejection.
- Missing/unsupported Content-Type or Content-Encoding, duplicate/invalid length headers,
  unknown query selectors and excessive query length fail safely before repository access.
- GET snapshot listing is bounded by the existing 1..100 repository limit.
- Known error categories include INVALID_MAPPING_INPUT (422), MAPPING_ALREADY_EXISTS (409),
  MAPPING_REVISION_CONFLICT (409), REQUIREMENT_REVIEW_CONFLICT (409),
  REQUIREMENT_NOT_ELIGIBLE (409), UNKNOWN_CATALOG_SKILL_UUID (422),
  INVALID_MAPPING_SCOPE (404), SNAPSHOT_NOT_FOUND (404), and DATABASE_FAILED (503).
- Disabled routes return CAPABILITY_MAPPING_API_DISABLED (404). Malformed oversized JSON
  returns MAPPING_REQUEST_TOO_LARGE (413). Unexpected failures return
  CAPABILITY_MAPPING_FAILED (500), without source strings or stack traces.
- POST operations are not idempotent; read current state after uncertain transport outcomes.
- No CLI subprocesses, file paths, user-controlled database configuration or model calls.
- **Source quote verified** is not the same as correct interpretation.
  **Review confirmed** is not fulfillment.
  **Skill mapping approved** is not qualification, certification, suitability,
  reserved capacity or tender Go/No-Go.

## React integration

The standalone [Skill Mapping view](manual-skill-mapping-ui.md) selects an analysis and one
imported snapshot, shows the overview/catalog, accepts explicit UUID selections, creates a
DRAFT and separately confirms/approves it. Snapshot import and mapping flags remain independent.
The draft selection contract has no expected-review fields; the API captures its current binding,
so the UI checks the returned draft against the proposed review and reloads after every attempt.
Approval's revision/review checks remain authoritative. No automatic approvals, CV parsing or
semantic matching are included. Revision/revocation/history and matching/report UI remain out of scope.
