# Milestone 3B-a: exploratory real-document retrieval

On **2026-10-10**, starting from main `14b6bbd` (merged PR #10), we ran actual
multilingual E5 retrieval against the externally acquired EU Tenders QA pilot.
**These are exploratory measurements against upstream candidate document labels.**
They are not independently human-verified accuracy, source-span retrieval, answer
correctness or end-to-end requirement extraction. No LLM was configured or called.

## Eligibility and indexed corpus

Dataset: `tmskss/eu-tenders-with-questions-for-agentic-checklist-filling`, immutable
revision **`f2b0856ef0a5c327afb4f40c7cde5dd44c8d56ea`**, selected family
**`BSGEE_2025-002_School_Information_Systems`**. The upstream card declares EN;
this run supplies no new German-quality evidence.

Local artifacts were present in this environment, but were rechecked rather than
assumed usable. We loaded the manifest/report and revalidated bytes, upstream
checksums, metadata and parsing offline through PR #10's acquisition workflow.
The same 12 cases yielded **9 eligible and 3 excluded**, with **0 unanswerables**
in this family. All four PDF bytes are available; the existing parser rejects the
encrypted invitation. We retained every original QA type and reference list;
excluded cases were not reduced to their remaining parseable references.

All **three parseable PDFs** were indexed together through `PyPdfParser`,
`chunk_pages`, `SqliteRepository`, the existing E5 provider, `RetrievalService`
and persistent `ChromaVectorStore`. This calls the production service directly,
without HTTP timing, API/UI changes or production settings/data-directory changes.
The isolated SQLite/Chroma directories hold 137 parsed pages and **306 chunks**.
Evaluation document IDs use deterministic UUIDv5 values derived from verified
PDF SHA-256 hashes; the existing repository also deduplicates identical bytes.

| Alias | Original upstream filename | TenderCite document ID | Pages | Chunks |
| --- | --- | --- | ---: | ---: |
| S | `02.BSGEE 2025-002 Specifications - FINAL.pdf` | `53314a87-d78d-5260-a617-c9ecef893c32` | 50 | 130 |
| A | `03.BSGEE 2025-002 Specifications - ANNEXES A B C D.pdf` | `cab0f7f9-b8d9-55a8-be20-baa7cdcb926b` | 37 | 50 |
| C | `09.BSGEE 2025-002 FwC_services - FINAL.pdf` | `24827d9b-3260-5da1-86b3-cc5a828a557e` | 50 | 126 |

The encrypted file is `01.BSGEE 2025-002 Invitation.pdf` (alias I, no indexed ID).
All exclusions retain their original IDs, types, referenced documents and reasons:

| Original QA ID | Upstream type | Original reference list | Exclusion reason |
| --- | --- | --- | --- |
| `BSGEE_2025-002_School_Information_Systems-b0043663` | Single-document lookup | I | Invitation: encrypted PDFs are not supported |
| `BSGEE_2025-002_School_Information_Systems-aad3fe25` | Cross-document factual | C, I | Invitation: encrypted PDFs are not supported |
| `BSGEE_2025-002_School_Information_Systems-d6a6ae3e` | Implicit multi-hop | C, A, S, I | Invitation: encrypted PDFs are not supported |

Any missing artifact, checksum mismatch or unsuccessful parsing similarly excludes
an entire referencing case; invalid metadata aborts rather than silently repairing
labels. Unanswerable cases, if present in a future selected family, are explicitly
excluded from positive-reference metrics and retain their IDs/reasons.

## Retrieval protocol and actual scores

Each eligible **original question, unchanged**, is passed to `RetrievalService.search`.
Every query is filtered to **all three indexed family document IDs**, never just its
expected documents. Search uses cosine similarity and the production maximum of
**50 ranked chunks per query**. A document's first appearance determines its unique
document rank; repeated chunks do not consume document k. Every hit's filename/ID
must match the indexed-family mapping. We report document k = **1, 2, 3**; reporting
5 would add nothing for this three-document corpus.

- **Any referenced document at k:** at least one expected document appears within
  the first k unique documents.
- **All referenced documents at k:** every expected document appears there.
- **Single-reference MRR:** mean reciprocal unique-document rank over the five
  cases with exactly one referenced filename; a document absent from the bounded
  chunk pool contributes zero. Multi-reference cases are excluded from this MRR.

These are ranks **within the bounded 50-chunk pool**, not a guaranteed complete
ranking of every indexed document. All nine queries returned 50 chunks; seven
queries exposed only two unique documents. Absence from this pool is not proof of
absence from the corpus. No upstream chunk ordinals are interpreted as PDF pages,
TenderCite chunk IDs or quote offsets. No source-span Hit@k or extraction F1 is computed.

| Candidate-label metric | k=1 | k=2 | k=3 |
| --- | ---: | ---: | ---: |
| Any referenced document | 9/9 (100%) | 9/9 (100%) | 9/9 (100%) |
| All referenced documents | 5/9 (55.56%) | 9/9 (100%) | 9/9 (100%) |

**Single-reference document MRR = 1.0 (5 cases).** Both real runs reproduced these
metrics and the same unique-document order for every case.

| Upstream type | Eligible cases | Any@1/@2/@3 | All@1 | All@2/@3 | Single-reference MRR |
| --- | ---: | --- | ---: | --- | --- |
| Single-document lookup | 2 | 2/2 at each k | 2/2 | 2/2 at each k | 1.0 (2 cases) |
| Single-document reasoning | 3 | 3/3 at each k | 3/3 | 3/3 at each k | 1.0 (3 cases) |
| Cross-document factual | 2 | 2/2 at each k | 0/2 | 2/2 at each k | Not applicable |
| Implicit multi-hop | 2 | 2/2 at each k | 0/2 | 2/2 at each k | Not applicable |

Per-case results below use the filename/ID aliases above. All cases also have
Any@2, Any@3 and All@3 = true. Local `results.json` retains the full original
questions, exact reference filenames/IDs, chunk hits, document ranks and metrics.

| Original QA ID | Upstream type | Expected documents (original order) | Retrieved unique document order | Any@1 | All@1 | All@2 | Single-reference RR |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `BSGEE_2025-002_School_Information_Systems-dc506b77` | Single-document lookup | S | S → C | true | true | true | 1.0 |
| `BSGEE_2025-002_School_Information_Systems-99401175` | Single-document lookup | S | S → C → A | true | true | true | 1.0 |
| `BSGEE_2025-002_School_Information_Systems-10671deb` | Single-document reasoning | S | S → C | true | true | true | 1.0 |
| `BSGEE_2025-002_School_Information_Systems-d6910bdf` | Single-document reasoning | S | S → C | true | true | true | 1.0 |
| `BSGEE_2025-002_School_Information_Systems-ab268553` | Single-document reasoning | S | S → A → C | true | true | true | 1.0 |
| `BSGEE_2025-002_School_Information_Systems-df0f34c8` | Cross-document factual | C, S | S → C | true | false | true | N/A |
| `BSGEE_2025-002_School_Information_Systems-0a938a30` | Cross-document factual | C, S | C → S | true | false | true | N/A |
| `BSGEE_2025-002_School_Information_Systems-a30e4536` | Implicit multi-hop | C, S | S → C | true | false | true | N/A |
| `BSGEE_2025-002_School_Information_Systems-d8adf980` | Implicit multi-hop | C, S | C → S | true | false | true | N/A |

## Failures and interpretation limits

There were **no Any@1 or All@2 misses among eligible cases**. The four All@1 misses
are structurally expected for two-reference cases: one unique document cannot
cover two expected documents. The three encrypted-source exclusions above remain
unresolved; no decryption, reference repair or replacement source was attempted.

**Label concentration makes these scores easy to achieve.** All nine eligible
cases reference S; all five single-reference cases reference S alone; all four
multi-reference cases reference the same C/S pair. No eligible label references A.
Consequently, an unevaluated fixed S-first ranking would satisfy Any@1 and the
single-reference MRR, while a fixed S/C order would satisfy All@2. This follows
from the label sets, not from an additional measured retrieval experiment.
Three documents, one family and selection against invitation-dependent cases
provide little evidence about discrimination among diverse tenders or hard negatives.

A correct document may still yield irrelevant chunks or incomplete evidence.
The upstream chunk boundaries/page spans cannot be reconstructed, and the card's
LLM-based curation supplies no independently verifiable human review. We did not
compare answers, infer quotes, modify labels or promote them into human VERIFIED
gold. Real source-span evaluation needs independently reviewed page/quote labels
or reliable original chunk exports. Original-source provenance, privacy and reuse
rights remain unverified; see the [acquisition report](external-benchmark-pilot.md).

## Model, configuration and runtime

The official Hugging Face SDK downloaded **`intfloat/multilingual-e5-small`** at
**`614241f622f53c4eeff9890bdc4f31cfecc418b3`** into ignored local evaluation storage.
Official model metadata resolved that exact SHA; the safetensors bytes matched
upstream LFS SHA-256 **`1a55775f53449dac10a2bcbc312469fac40b96d53198c407081a831f81c98477`**.
SentenceTransformers loaded the verified immutable snapshot locally with
`trust_remote_code=False`, CPU float32, and maximum sequence length 512. The final
run verified a normalized **384-dimensional query vector and all 306 passage vectors**.
The second run reused the verified weight cache. **No network/model blocker occurred**;
TLS verification and the managed proxy remained enabled.

The production page-bounded chunker uses 1,200 characters with 150-character
overlap; indexing embeds batches of at most 64. E5 receives `query: ` / `passage: `
prefixes and normalizes vectors. Actual prefixed passage lengths peaked at
**431 tokens**; **0** exceeded the 512-token model limit. These checks do not
validate PDF table extraction or the semantic correctness of upstream references.

Final measurement: **2026-10-10T19:53:45.970284+00:00**, Python **3.12.14**,
Linux-6.18.44-x86_64-with-glibc2.41, **INTEL(R) XEON(R) PLATINUM 8573C**. Five CPUs were visible/in affinity;
cgroup `cpu.max` was `400000 100000` (four CPU-equivalents). Torch used
four intra-op threads, one inter-op thread; tokenizer parallelism was disabled.

| Dependency | Actual version |
| --- | --- |
| chromadb | 1.5.9 |
| huggingface-hub | 1.33.0 |
| numpy | 2.5.3 |
| pypdf | 6.19.0 |
| sentence-transformers | 6.1.0 |
| torch | 2.14.1+cpu |
| transformers | 5.19.0 |

| Wall-clock measurement | Actual seconds |
| --- | ---: |
| Final run: cached metadata/snapshot resolution + weight hashing | 1.275 |
| Final run: model loading + first query probe | 8.757 |
| Final run: PDF parse/chunk/persist total | 15.765 |
| Final run: embedding + Chroma indexing total | 106.614 |
| Final run: query min / median / mean / max (nine queries) | 0.116 / 0.135 / 0.150 / 0.248 |
| Final run: total including validation/imports/model/index/token audit/queries | 157.239 |
| Initial real run: fresh weight download/metadata/hash | 14.367 |
| Initial real run: total | 101.046 |

Query latency includes E5 encoding, Chroma querying and repository verification,
without HTTP. Index duration combines embeddings and persistence; no separate
embedding-only throughput is claimed. Timings are individual observations, not
latency guarantees; cache state, caller environment and uncontrolled resource
conditions differ between runs. Both raw records are preserved locally.
The final runner SHA-256 is `16c9c9eb0798423898f3b8d5811792e06ff309101f4755ff451030df16794c5a`.
Local results also record all three evaluation-source hashes, lock hash, Git commit
(`a60f2c621de61d3013bf107c02c35b4aa16c7464`), and worktree state: the runner/report were pending their
final commit at measurement time. The committed runner has the measured hash.

## Artifact integrity and reproduction

All six acquired artifact hashes were checked again; PDFs/QA and original
retrieval timestamps remain unchanged. The table publishes identifiers/checksums,
not third-party questions, answers or source text.

| Artifact | Verified SHA-256 |
| --- | --- |
| `README.md` | `92fc0a9aab6001eef2774c21056aa5eb6442f4f0dcc08d1d9264d519c8bc1fc4` |
| `eu-tenders-with-questions-for-agentic-checklist-filling.json` | `5453b298c7fd154b2eb896efd0ce6e77636e0a19d3d5287cb7504c8d94928a00` |
| `01.BSGEE 2025-002 Invitation.pdf` | `580db1d72c59882b4267fb5b413aeb19ecba0581414449b3a82d34fe79f685db` |
| `02.BSGEE 2025-002 Specifications - FINAL.pdf` | `c1f60eaec29d35ebcb14790948566080b1d06cd5eabb7cebb2a09c3918c3efe7` |
| `03.BSGEE 2025-002 Specifications - ANNEXES A B C D.pdf` | `b0081a3ff075c6211721018a829338ed12c784ae4e577db2f3d12f1a177118be` |
| `09.BSGEE 2025-002 FwC_services - FINAL.pdf` | `113e3a3cd38a04f185493f2816f13130e0cbb2c0a19644b2fb177e77327342c5` |

From the repository root with the existing locked retrieval/development environment,
use these exact one-line **PowerShell** commands. Equivalent module invocations
ran on Linux; Windows execution was not tested. Use a fresh output directory for
each measurement; an existing directory is rejected to preserve observations.

```powershell
.\.venv\Scripts\python.exe -m evaluation.acquire_external_benchmark --revision f2b0856ef0a5c327afb4f40c7cde5dd44c8d56ea --family BSGEE_2025-002_School_Information_Systems --download-pdfs --output-dir .\data\evaluation\external\eu-tenders-qa-f2b0856-school-systems
.\.venv\Scripts\python.exe -m evaluation.external_document_retrieval --acquisition-dir .\data\evaluation\external\eu-tenders-qa-f2b0856-school-systems --output-dir .\data\evaluation\external\document-retrieval-run-2 --model-cache .\data\evaluation\external\multilingual-e5-cache --run-real-model
```

Acquisition is expected to exit **1** because the encrypted invitation fails
parsing; inspect that report before proceeding. The retrieval runner independently
revalidates and evaluates the eligible cases, retaining all exclusions. Its real
runs exit **0**. Reproduction after a previous run requires a new output name
(e.g. `document-retrieval-run-3`); no existing result is overwritten.

Full local results are `data/evaluation/external/document-retrieval-run-1/results.json`
and `data/evaluation/external/document-retrieval-run-2/results.json`. Downloaded
weights/cache, QA, PDFs, extracted text, vectors, queries and raw hits stay under
ignored `data/evaluation/external/`. No external questions or reference answers,
PDFs, weights, new providers, dependencies or human-gold modifications enter Git.
Model/download/index failures return nonzero and save the stage, actual error,
hostname/status where available, and observed proxy-denial evidence without metrics.

Offline validation: **29 new deterministic tests; full suite 285 passed, 1 optional
live TED test skipped; Ruff lint and formatting passed.** Coverage includes unchanged
multi-reference eligibility/exclusions, encryption, missing/tampered artifacts,
unique-document ranking/mapping, metrics/empty results, family isolation, persistent
Chroma with fake embeddings, model checksum/revision failures and explicit live opt-in.
No external datasets, live embeddings or paid APIs are required in CI.
