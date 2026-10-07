# Release gate record

This is an unreleased development build on v0.1, **not v1.0.0**.
Validation date: 2026-10-07. See `docs/work-log.md` for phase commits.

| Gate | Evidence / status |
| --- | --- |
| Bootstrap inspected | Started from GitHub main 7785ec7; existing architecture retained |
| Source ingestion and evidence | Text-bearing multi-page PDF tests, exact offsets, real/fabricated quotes |
| Retrieval plumbing | Real persistent Chroma with deterministic embeddings; dedup, filters, source metadata and deletion |
| Structured extraction | Deterministic fake StructuredLLM and HTTP transport contract tests |
| Reviews, matrix, exports | API integration tests preserve original output/history and source references |
| UI | Streamlit AppTest against actual API; unsupported evidence styling and save-review action |
| Hardening | Invalid/encrypted/oversized PDFs, limits, filename paths, cleanup, provider bounds and secret redaction |
| Test/lint baseline | 35 tests passed in clean clone; 93% backend coverage; Ruff/format passed |
| Dependency install | Fresh local clone and new virtual environment; hashed Python 3.12 CPU install passed |
| Docker build | Built successfully with trusted cloud proxy CA mounted as BuildKit secret |
| Docker functionality | Non-root API: PDF upload, pages/chunks, evidence and restart persistence passed; UI AppTest rendered every section against the container API and exposed all three exports |
| Live E5 retrieval | BLOCKED: model download received HTTP 403 from cloud egress proxy |
| Live structured extraction | NOT RUN: no real provider/model configured |
| Real-model evaluation | NOT RUN: requires E5 download and configured LLM; no accuracy scores claimed |
| Audit | Four Chroma server advisories, not exposed by embedded design; torch lookup skipped; see security review |
| Licenses | Principal dependency licenses documented; model revision/weights not downloaded/reviewed here |
| Remote CI | Workflow updated; no hosted CI run claimed for unpushed local commits |
| Release tag | Not created; external validation gates remain open |

A published CI run, real model evaluation and explicit review of remaining findings are required
before declaring v1.0.0. Fake tests do not substitute for live semantic retrieval or provider output.

Final local verification uses code commit `c9c9e28` plus the internal-service proxy configuration
fix. The clean clone ran 35 tests (none skipped), with 93% backend statement coverage. One
upstream Starlette/httpx deprecation warning remains; it does not change test outcomes.
Docker Compose configuration validates. Local API/UI startup and configuration/document requests
return successfully. No tracked `.env`, key, certificate, database or uploaded PDF artifacts
were found; this is not a claim of a comprehensive historical secret scan.
