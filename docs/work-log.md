# Incremental implementation record

Started from `7785ec7` (v0.1 bootstrap), fast-forwarding the earlier empty local checkout.
Application architecture was retained; new modules implement the sequential feature phases.
All statements below concern local checks, not remote CI or a published release.

| Phase | Commit | Main changes | Validation at phase boundary |
| --- | --- | --- | --- |
| 0 | c9295df | FastAPI annotations, trimmed chunk offsets, real PDF fixture | 9 tests; Ruff/format; API HTTP health |
| 1 | 501c9a0 | E5/Chroma interfaces and adapters, search/index API, dedup | 12 tests; Ruff/format; actual E5 download blocked by proxy |
| 2 | faeb587, c642a2d | Extraction schemas/service, provider contract, analysis/finding persistence | 15 tests; Ruff/format; API startup |
| 3 | d0bfde9 | Immutable review events and effective reviewed values | 17 tests; Ruff/format; API startup |
| 4 | f9fea21 | Human-managed matrix, conservative defaults, stale assignment handling | 18 tests; Ruff/format; API startup |
| 5 | 2dd6df2 | JSON/CSV/Markdown exports and injection-safe rendering | 20 tests; Ruff/format; API startup |
| 6 | 8ca8d58 | Streamlit workflow and safe configuration summary | 22 tests; Ruff/format; Streamlit HTTP health and AppTest |
| 7 | cfc0c29 | Synthetic six-case gold corpus, metrics, live evaluation runner | 24 tests; Ruff/format; live model measurements pending |
| 8 | f992311 | Upload/provider/storage hardening, hashes, CI, Docker, advisory review | 34 tests; Ruff/format; Docker built with verified proxy CA |
| 9 | e8d78ac | Documentation synchronized with implemented behavior and open gates | 34 tests; Ruff/format |

Architecture choices: SQLite remains the source of truth; Chroma is rebuildable; extraction
validates every evidence item; reviews never replace original model output; matrix decisions
are human inputs. Hosted model credentials are never required by offline tests. External model
validation remains outstanding rather than being silently replaced with passing fake scores.

Final verification: `4513105` fixes container frontend ownership; `c9c9e28` retains original
structured candidates before quote normalization. A clean clone and new hashed installation
pass 35 tests with 93% backend coverage. Docker API upload/evidence/restart checks and UI
AppTest against the container API pass. The final Compose fix preserves external proxy settings
and appends the internal API host to both uppercase and lowercase no-proxy variables.

Live model gates remain external blockers: Hugging Face is absent from active egress policy,
and no LLM base URL/model/key is configured. No release tag, remote CI pass or push is claimed.
