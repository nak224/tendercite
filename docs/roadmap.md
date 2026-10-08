# Roadmap to 2026-10-26

The v0.1 foundation is extended with retrieval, grounded extraction, reviews, a human-managed
matrix, exports, UI, synthetic evaluation, hardening and documentation. This is unreleased work;
implemented paths do not by themselves establish v0.5/v1 production readiness.

## Required before a v1.0.0 tag

- Review the [completed real multilingual E5 evaluation](evaluation/multilingual-e5-synthetic-tender-3.md).
  Revision `614241f622f53c4eeff9890bdc4f31cfecc418b3` is loaded, verified and pinned.
- Investigate observed context misses and expand beyond seven synthetic cases per language.
  The category plan covered 11/14 spans versus 12/14 for the broad-query baseline; no general
  real-tender quality or exhaustive coverage claim is justified.
- Configure an actual OpenAI-compatible provider and run structured extraction/evidence evaluation.
- Inspect failed/unsupported findings manually; publish narrow, reproducible measurements.
- Verify Docker model/provider access, not only ingestion and liveness.
- Run CI on the pushed commits; review the known Chroma advisory applicability and audit gaps.
- Confirm clean-clone instructions, licenses, documentation and changelog after final changes.
- Complete [the release checklist](release-check.md); tag only when gates pass.

## RET-1 — Analysis retrieval coverage

**Implemented:** an explicit, versioned plan searches the user query and six fixed bilingual
category queries separately (three hits each), deduplicates by chunk ID with maximum similarity,
and selects context round-robin within a bounded budget. Analysis snapshots retain the plan,
per-query candidate IDs and final context IDs. Offline fake-embedding regressions demonstrate
category contributions across selected documents where one broad query omits them, and preserve
all citation checks. See [architecture](architecture.md#category-aware-analysis-retrieval-ret-1).

**Remaining limitation:** bounded semantic retrieval still does not guarantee every requirement,
subtopic or document contributes context. Exhaustive tender extraction is not claimed. Real-model
coverage has been measured on the pinned revision for 14 synthetic cases, with three German
context misses. Representative real-tender evaluation remains open.

## Follow-up quality work

Expand beyond the 14 English/German synthetic cases with multi-chunk pages and procurement noise;
measure German retrieval quality and test ambiguity, long documents and tables. The default
model now has a first narrow synthetic measurement; generalization remains unmeasured.
Add selective workspace/analysis deletion with explicit audit/privacy semantics, pagination,
background indexing and optimistic concurrency if real usage requires them. Consider Docling/OCR
only after a measured parser need. Add screenshots from a verified demonstration.

## Non-goals for v1

Kubernetes, microservices, autonomous bidding agents, legal decisions, bidder capability inference,
authentication/multi-tenancy/RBAC, custom model training, universal OCR, DOCX and generated PDF export.
Prefer a smaller measured release over unsupported capability claims.
