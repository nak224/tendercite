# Changelog

## Unreleased

- Stabilize original Ruff checks and correct trimmed chunk offsets.
- Add CPU E5 embedding and persistent Chroma adapters with document-scoped search,
  idempotent indexing and byte-identical PDF deduplication.
- Add provider-neutral structured extraction, schema validation, per-citation grounding,
  immutable analysis snapshots and original findings persistence.
- Add append-only human reviews and separately derived effective values.
- Add human-managed Go / No-Go assessments with conservative defaults and stale-review invalidation.
- Add source-linked JSON/CSV/Markdown exports and complete Streamlit workflow.
- Add six-case synthetic evaluation, metric tests and a live evaluation runner.
- Bound PDF/provider inputs, harden cleanup, storage, filenames and exports; document security limits.
- Add hashed Python 3.12 CPU locks, non-root Docker runtime and expanded offline CI checks.
- Update API, deployment, architecture, licensing and handover documentation.
- Live E5/provider evaluation remains pending; no v1.0.0 release is declared.

## 0.1.0 — 2026-10-07

Initial evidence-grounded foundation: FastAPI, PDF upload, page-aware pypdf extraction,
page-bounded chunking, SQLite persistence, deterministic quote validation, initial Streamlit
UI, Docker/CI definitions, tests, Apache-2.0 licensing and project documentation.
