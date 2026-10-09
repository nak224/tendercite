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
| Test/lint | Real-evaluation follow-up: 65 offline tests passed, none skipped; Ruff/format and Compose configuration passed. Historical core clean-clone baseline: 35 tests, 93% coverage |
| Dependency install | Fresh local clone and new virtual environment; hashed Python 3.12 CPU install passed |
| Docker build | Built successfully with trusted cloud proxy CA mounted as BuildKit secret |
| Docker functionality | Non-root API: PDF upload, pages/chunks, evidence and restart persistence passed; UI AppTest rendered every section against the container API and exposed all three exports |
| Embedding reproducibility | PASS: official revision `614241f622f53c4eeff9890bdc4f31cfecc418b3` downloaded, safetensors SHA-256 verified, SentenceTransformers loaded, normalized 384D query/passage vectors checked; tested default pinned and configurable |
| Live E5 retrieval | MEASURED: real PDF/FastAPI/Chroma, 14 cases, 3 repeated trials. Per-language source-span Hit@1 6/7, Hit@3/@5 7/7. [Report and raw results](evaluation/multilingual-e5-synthetic-tender-3.md) |
| German retrieval quality | NARROW SYNTHETIC MEASUREMENT: 7 cases only; reference query ranks its gold span second. General real-tender quality remains unestablished |
| Analysis coverage | MEASURED LIMITATION: budget 12 gives 11 unique category-plan chunks and 11/14 gold spans; broad-query baseline gives 12 chunks and 12/14 spans. Plan misses German deadline, references and security. Not exhaustive |
| Live structured extraction | NOT RUN: no real provider/model configured |
| Real-model evaluation | Retrieval/context coverage measured on unchanged synthetic-tender-3. No LLM configured/called; extraction, evidence quality and real-tender evaluation remain open |
| Audit | Four Chroma server advisories, not exposed by embedded design; torch lookup skipped; see security review |
| Licenses | Official pinned card/API declare MIT; model/configuration inspected and weight hash verified. No weights redistributed. Existing dependency audit/license review still applies |
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

The first download attempt from merged PR #2 / main `c85f325` was blocked by the proxy.
Its [historical diagnostic record](evaluation/multilingual-e5-synthetic-tender-3-blocked.md)
is retained. After the allowlist update, the official snapshot downloaded through the managed
proxy with TLS verification. The [completed evaluation](evaluation/multilingual-e5-synthetic-tender-3.md)
records actual metrics, misses, timings, versions and reproduction steps. No retrieval tuning,
external LLM configuration/call, real-tender accuracy claim or v1.0.0 tag followed from this run.

The complete offline suite now passes 65 tests, including recalculation of all recorded query
and context metrics from raw hits/chunk text. Ruff lint/format and Compose configuration pass.
No new Docker runtime or model/provider deployment validation is claimed for this follow-up.
