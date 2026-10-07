# Release plan to 2026-10-26

The schedule favors a narrow, reliable vertical slice over broad but unverified functionality.

## 7–8 October — v0.1 provenance foundation

**Goal:** the repository is public-ready and the core source-traceability invariant exists in code.

Exit criteria:

- FastAPI project starts locally.
- PDF upload validates file type and size.
- Each page is stored with a stable 1-based page number.
- Chunks never cross page boundaries.
- Document/page/chunk metadata persists in SQLite.
- Evidence quotes can be deterministically validated or rejected.
- Tests pass in CI configuration.
- Apache-2.0, contribution, security and third-party license notes exist.
- Docker API and minimal Streamlit UI are defined.

## 9–11 October — retrieval

**Goal:** useful semantic search across several tender PDFs.

Deliverables:

- sentence-transformers embedding adapter.
- Default embedding model: `intfloat/e5-small-v2`.
- Local Chroma collection keyed by chunk ID.
- Multi-document indexing.
- `POST /api/v1/search` with filters by document.
- Search UI showing document, page, quote/context and similarity score.
- Retrieval tests with a small synthetic fixture corpus.

## 12–15 October — structured extraction

**Goal:** turn retrieved evidence into typed tender findings.

Deliverables:

- Pydantic extraction schema.
- Provider-neutral `StructuredLLM` wiring.
- OpenAI-compatible provider configuration for local or hosted models.
- Requirement category + requirement type classification.
- Extraction coverage for deadlines, eligibility, references, financial/insurance, technical/certificates, award/price, contract/liability, privacy/security and risk.
- Every generated finding must carry at least one source candidate.
- Server-side evidence validation after generation.
- Invalid/missing evidence stays visibly ungrounded and cannot be presented as verified.

## 16–18 October — human review

**Goal:** findings become auditable working objects rather than ephemeral model output.

Deliverables:

- Findings persistence.
- Confirm / modify / reject actions.
- Comments.
- Append-only review history preserving original model output.
- UI filters for unreviewed/confirmed/modified/rejected findings.

## 19–20 October — Go/No-Go + export

**Goal:** create a practical decision aid without autonomous legal claims.

Deliverables:

- Matrix rows generated from reviewed findings.
- Human-set assessment statuses.
- Rationale and evidence shown together.
- JSON, CSV and Markdown export.

## 21–22 October — evaluation and test fixtures

**Goal:** show that the system is tested on more than happy-path unit tests.

Deliverables:

- Redistributable or synthetic tender-like PDF fixture set.
- Gold annotations for a small set of requirements and citations.
- Retrieval checks such as Recall@k on the fixture corpus.
- Extraction precision/recall or per-field exact-match where meaningful.
- Citation validity rate reported separately from extraction correctness.

## 23–24 October — portfolio polish

**Goal:** a third party can understand, run and evaluate TenderCite.

Deliverables:

- README screenshots/GIF.
- Architecture diagram.
- Example analysis walkthrough.
- Complete API/deployment docs.
- Clear limitations and privacy notes.
- GitHub issue templates / release checklist if time permits.

## 25 October — hardening

- Clean install from a fresh environment.
- Docker Compose smoke test.
- CI green on main.
- Dependency/model license review.
- Security review of uploads, secrets and remote-provider configuration.
- Remove dead code and unsupported claims.

## 26 October — v1.0.0

Tag `v1.0.0` only if all v1.0 criteria below are true. Otherwise publish an honest `v0.x` rather than relabeling incomplete work.

# Version definitions

## v0.1

TenderCite has a working provenance foundation: PDF ingestion, page tracking, page-bounded chunks, local persistence, evidence validation, tests and API/UI skeleton.

## v0.5

TenderCite is a genuinely useful analysis prototype: multi-PDF semantic retrieval and structured requirement extraction work end to end, findings are typed, and every purported source citation is checked against stored page text.

## v1.0

TenderCite demonstrates the complete portfolio claim:

- document import;
- semantic retrieval;
- structured requirement extraction;
- verified evidence linking;
- human review with preserved machine output;
- Go/No-Go matrix;
- JSON/CSV/Markdown export;
- documented REST API;
- usable web UI;
- tests and small evaluation fixture;
- Dockerized local run;
- CI passing;
- architecture/deployment/security/contribution documentation;
- Apache-2.0 for TenderCite code plus explicit third-party/model/data license notes;
- no known critical security issue or knowingly unsupported README claim.
