# Release gate record

This is an unreleased development build on v0.1, **not v1.0.0**.
Latest offline validation: 2026-10-08. Historical core checks below were run on 2026-10-07;
see `docs/work-log.md` for those phase commits.

| Gate | Evidence / status |
| --- | --- |
| Bootstrap inspected | Started from GitHub main 7785ec7; existing architecture retained |
| Source ingestion and evidence | Text-bearing multi-page PDF tests, exact offsets, real/fabricated quotes |
| Retrieval plumbing | Real persistent Chroma with deterministic embeddings; dedup, filters, source metadata and deletion |
| Structured extraction | Deterministic fake StructuredLLM and HTTP transport contract tests |
| Reviews, matrix, exports | API integration tests preserve original output/history and source references |
| UI | Streamlit AppTest against actual API; unsupported evidence styling and save-review action |
| Hardening | Invalid/encrypted/oversized PDFs, limits, filename paths, cleanup, provider bounds and secret redaction |
| Test/lint | RET-1: 63 offline tests passed, none skipped; Ruff/format passed. Historical core clean-clone baseline: 35 tests, 93% coverage |
| Dependency install | Fresh local clone and new virtual environment; hashed Python 3.12 CPU install passed |
| Docker build | Built successfully with trusted cloud proxy CA mounted as BuildKit secret |
| Docker functionality | Non-root API: PDF upload, pages/chunks, evidence and restart persistence passed; UI AppTest rendered every section against the container API and exposed all three exports |
| Embedding reproducibility | OPEN: a concrete verified multilingual-e5-small revision must be pinned before live evaluation / v1.0.0; none is verified yet |
| Live E5 retrieval | NOT RUN for multilingual-e5-small; the prior onboarding model download received HTTP 403 from the cloud proxy |
| German retrieval quality | NOT EVALUATED: multilingual support is enabled by model choice; bilingual fixtures are offline coverage only |
| Analysis coverage | RET-1 implemented: bounded bilingual category plan, deduplication and audit metadata; offline regressions cover multiple categories/documents. Live coverage remains unmeasured and extraction is not exhaustive |
| Live structured extraction | NOT RUN: no real provider/model configured |
| Real-model evaluation | NOT RUN: requires E5 download and configured LLM; no accuracy scores claimed |
| Audit | Four Chroma server advisories, not exposed by embedded design; torch lookup skipped; see security review |
| Licenses | Principal dependency licenses documented; model revision/weights not downloaded/reviewed here |
| Remote CI | Hosted CI for the current PR revision must be checked; no result is claimed here |
| Release tag | Not created; external validation gates remain open |

A published CI run, real model evaluation and explicit review of remaining findings are required
before declaring v1.0.0. Fake tests do not substitute for live semantic retrieval or provider output.

Original core verification (before the multilingual follow-up) used commit `c9c9e28` plus
the internal-service proxy configuration fix. The clean clone ran 35 tests (none skipped), with 93% backend statement coverage. One
upstream Starlette/httpx deprecation warning remains; it does not change test outcomes.
Docker Compose configuration validates. Local API/UI startup and configuration/document requests
return successfully. No tracked `.env`, key, certificate, database or uploaded PDF artifacts
were found; this is not a claim of a comprehensive historical secret scan.

The targeted multilingual follow-up adds six German cases alongside the six original English
cases, with shared requirement pages and distractors. All 38 offline tests, Ruff and formatting
checks pass. No live model download, pinned revision or retrieval-quality result is claimed.

RET-1 starts from merged main `8c9d293`. The category plan, audit metadata, bounded selection
and evidence-boundary regressions pass offline. Dataset v3 retains all 12 previous cases and
adds two independent SECURITY cases, multi-chunk pages and source-span retrieval metrics.
These tests use fake embeddings/providers; no live quality or new coverage percentage is claimed.
