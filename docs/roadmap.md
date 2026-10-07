# Roadmap to 2026-10-26

The v0.1 foundation is extended with retrieval, grounded extraction, reviews, a human-managed
matrix, exports, UI, synthetic evaluation, hardening and documentation. This is unreleased work;
implemented paths do not by themselves establish v0.5/v1 production readiness.

## Required before a v1.0.0 tag

- Download and verify a concrete `intfloat/multilingual-e5-small` revision; pin its commit in
  `TENDERCITE_EMBEDDING_REVISION` before live evaluation or v1.0.0. No revision is verified yet.
- Run real retrieval on both the English and German synthetic cases; model choice enables
  multilingual support but German retrieval quality still requires evaluation.
- Configure an actual OpenAI-compatible provider and run structured extraction/evidence evaluation.
- Inspect failed/unsupported findings manually; publish narrow, reproducible measurements.
- Verify Docker model/provider access, not only ingestion and liveness.
- Run CI on the pushed commits; review the known Chroma advisory applicability and audit gaps.
- Confirm clean-clone instructions, licenses, documentation and changelog after final changes.
- Complete [the release checklist](release-check.md); tag only when gates pass.

## RET-1 — Analysis retrieval coverage

**Open limitation:** the current analysis pipeline uses a single broad query and top-k retrieval.
It is not guaranteed to retrieve every requirement from long or multi-document tender packages.
TenderCite must not claim exhaustive tender extraction.

**Intended next improvement:** issue deterministic category-specific retrieval queries for
requirement categories, then deduplicate chunks by chunk ID before extraction. Evaluate coverage
on long and multi-document fixtures while retaining document/page identity. This improvement is
planned only; this PR follow-up does not change the retrieval pipeline or claim coverage gains.

## Follow-up quality work

Expand beyond the 12 English/German synthetic cases with shared requirement pages and noise;
measure German retrieval quality and test ambiguity, long documents and tables. The default
model now enables multilingual support; its quality remains to be measured.
Add selective workspace/analysis deletion with explicit audit/privacy semantics, pagination,
background indexing and optimistic concurrency if real usage requires them. Consider Docling/OCR
only after a measured parser need. Add screenshots from a verified demonstration.

## Non-goals for v1

Kubernetes, microservices, autonomous bidding agents, legal decisions, bidder capability inference,
authentication/multi-tenancy/RBAC, custom model training, universal OCR, DOCX and generated PDF export.
Prefer a smaller measured release over unsupported capability claims.
