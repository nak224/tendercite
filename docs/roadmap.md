# Roadmap to 2026-10-26

The v0.1 foundation is extended with retrieval, grounded extraction, reviews, a human-managed
matrix, exports, UI, synthetic evaluation, hardening and documentation. This is unreleased work;
implemented paths do not by themselves establish v0.5/v1 production readiness.

## Required before a v1.0.0 tag

- Download/pin the E5 model and run semantic retrieval against the synthetic fixture.
- Configure an actual OpenAI-compatible provider and run structured extraction/evidence evaluation.
- Inspect failed/unsupported findings manually; publish narrow, reproducible measurements.
- Verify Docker model/provider access, not only ingestion and liveness.
- Run CI on the pushed commits; review the known Chroma advisory applicability and audit gaps.
- Confirm clean-clone instructions, licenses, documentation and changelog after final changes.
- Complete [the release checklist](release-check.md); tag only when gates pass.

## Follow-up quality work

Expand the corpus beyond six English synthetic examples, measure German/multilingual retrieval,
test ambiguity, long documents and tables, and consider a multilingual embedding adapter.
Add selective workspace/analysis deletion with explicit audit/privacy semantics, pagination,
background indexing and optimistic concurrency if real usage requires them. Consider Docling/OCR
only after a measured parser need. Add screenshots from a verified demonstration.

## Non-goals for v1

Kubernetes, microservices, autonomous bidding agents, legal decisions, bidder capability inference,
authentication/multi-tenancy/RBAC, custom model training, universal OCR, DOCX and generated PDF export.
Prefer a smaller measured release over unsupported capability claims.
