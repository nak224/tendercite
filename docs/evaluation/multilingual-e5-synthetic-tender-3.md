# Real multilingual E5 retrieval evaluation

**Completed on 2026-10-08.** This supersedes the [blocked attempt](multilingual-e5-synthetic-tender-3-blocked.md).
[Raw results](multilingual-e5-synthetic-tender-3.json) retain every trial's ranked hits, similarity
scores, timings, source chunk text/offsets, gold quotes, query plan and environment. No LLM was
configured or called. Production retrieval behavior and `synthetic-tender-3` were not tuned.

## Model and method

Official model: `intfloat/multilingual-e5-small`, pinned revision
**`614241f622f53c4eeff9890bdc4f31cfecc418b3`**. Hugging Face revision metadata resolved this SHA;
its card and configuration were inspected, and the official `snapshot_download` completed.
Both previously blocked hosts now respond through the managed proxy (CAS root: 404, CDN root:
200), and the artifact download succeeded with TLS verification enabled. No proxy bypass or
alternative weight source was used. The MIT license is declared by the card/API. The local
safetensors SHA-256 matches the authoritative Hugging Face LFS SHA-256:
`1a55775f53449dac10a2bcbc312469fac40b96d53198c407081a831f81c98477`.

SentenceTransformers successfully loaded the pinned cache on CPU. Query and all 15 passage
vectors were verified as **384-dimensional, unit-normalized** (squared-norm tolerance 1e-5).
Configuration: float32, 12 BERT layers / 12 heads, XLM-R tokenizer, mean-token pooling plus
normalization, 512-token limit, `trust_remote_code=False`, `query: ` / `passage: ` prefixes.
The longest prefixed passage was 267 tokens: no corpus chunk was truncated by the model.
Weights remain in ignored `data/models/`; only hashes/configuration are committed. The runtime
default, `.env.example`, local ignored `.env` and Compose pin the tested revision; overrides
remain configurable. Changing model/revision requires reindexing.

The unchanged fixture contains **14 requirements (7 EN, 7 DE), 8 pages and 15 chunks**, using
normal 1,200-character chunking with 150-character overlap. A fresh isolated SQLite/Chroma
store was populated via real PDF upload to a real Uvicorn/FastAPI server on loopback HTTP.
Searches used `/api/v1/search`, real E5 embeddings, persistent Chroma cosine search and SQLite
source rehydration. The evaluation driver only times existing operations. Three sequential
trials used the same warmed model/index; rankings, contexts and metrics were identical.

## Individual query retrieval

Each gold query retrieves five chunks from the complete bilingual document. @1 and @3 use
prefixes of that ranked top-five response. Both metrics require the correct document/page;
source-span hits additionally require the complete normalized gold quote in one chunk.
These are same-language queries over a bilingual corpus, not an independent cross-language
translation benchmark. Repeating the same 14 cases does not increase the sample size to 42.

| Group | Page Hit@1 | Page Hit@3 | Page Hit@5 | Source-span Hit@1 | Source-span Hit@3 | Source-span Hit@5 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| English (7) | 6/7 (85.71%) | 7/7 (100%) | 7/7 (100%) | 6/7 (85.71%) | 7/7 (100%) | 7/7 (100%) |
| German (7) | 6/7 (85.71%) | 7/7 (100%) | 7/7 (100%) | 6/7 (85.71%) | 7/7 (100%) | 7/7 (100%) |
| Combined (14) | 12/14 (85.71%) | 14/14 (100%) | 14/14 (100%) | 12/14 (85.71%) | 14/14 (100%) | 14/14 (100%) |

The rank-1 misses are `references` and `de-references`: historical distractor pages 3 and 7
outrank the actual requirement spans on pages 1 and 5. Both correct spans rank second.
There are no query-level source-span misses at k=3 or k=5 in this fixture.

## Combined analysis context, separate from query metrics

The unmodified `category-aware-round-robin` plan, version `1`, uses the default English user
query plus six fixed bilingual category queries, each with three hits, and a **12-chunk budget**.
The production plan/selection functions execute their searches through the same HTTP endpoint;
no extraction endpoint or LLM is invoked. Exact query texts and per-query candidate IDs are in
JSON. Deduplication yields **11 unique chunks**, so the final context has 11 rather than 12;
the plan does not perform extra searches to fill unused slots. The prior broad-query baseline
uses the identical default user query and maximum budget, returning 12 unique chunks.

| Strategy | Actual chunks / budget | English span coverage | German span coverage | Combined span coverage |
| --- | ---: | ---: | ---: | ---: |
| Category plan | 11 / 12 | 7/7 (100%) | 4/7 (57.14%) | 11/14 (78.57%) |
| Previous single query | 12 / 12 | 7/7 (100%) | 5/7 (71.43%) | 12/14 (85.71%) |

**The category plan underperforms the broad-query baseline on this small fixture.** It misses
`de-deadline`, `de-references` and `de-security`; the baseline misses `de-deadline` and
`de-security`. Those spans do not enter the category plan's candidate union. Deadlines and
eligibility queries return only English-page chunks in their top three. This is an observation,
not proof of a general language bias or a recommendation to change retrieval based on 14 cases.
The strategies share a maximum budget but have different actual context sizes; this is a
comparison of their existing behavior, not a controlled equal-context-size ablation.

| Case | Language | First gold-span rank (top 5) | Category context | Broad-query context |
| --- | --- | ---: | --- | --- |
| deadline | en | 1 | yes | yes |
| references | en | 2 | yes | yes |
| insurance | en | 1 | yes | yes |
| certificate | en | 1 | yes | yes |
| price | en | 1 | yes | yes |
| privacy | en | 1 | yes | yes |
| de-deadline | de | 1 | **miss** | **miss** |
| de-references | de | 2 | **miss** | yes |
| de-insurance | de | 1 | yes | yes |
| de-certificate | de | 1 | yes | yes |
| de-price | de | 1 | yes | yes |
| de-privacy | de | 1 | yes | yes |
| security | en | 1 | yes | yes |
| de-security | de | 1 | **miss** | **miss** |

## Performance and reproducibility

One warm-model upload embedded/indexed all 15 chunks; model loading is timed separately.
HTTP timings include query embedding, Chroma lookup, SQLite rehydration, serialization and
loopback transport. Three ordered trials give 42 per-case requests, three seven-query plans
and three baseline requests. These shared-CPU timings are observations, not throughput/SLA
claims, independent load trials, or production percentile estimates.

| Operation | Observed time |
| --- | ---: |
| Cached model load/imports + first verification query | 5.783 s |
| Passage embedding (15 chunks, one service batch) | 1.242 s |
| Index operation, including embedding and Chroma upsert | 1.258 s |
| Full PDF upload/parse/chunk/store/embed/index | 1.280 s |
| Warm per-case top-five HTTP search, median (min–max), n=42 | 27.70 ms (24.70–39.95 ms) |
| Category plan over HTTP, median (min–max), n=3 | 228.01 ms (214.71–240.29 ms) |
| Broad-query top-12 HTTP search, median (min–max), n=3 | 36.28 ms (30.29–37.17 ms) |

Linux x86-64, AMD EPYC 9V74, five visible affinity CPUs with a four-CPU cgroup quota,
32 GiB memory limit; PyTorch uses four intra-op threads and one inter-op thread. Python
3.12.14, SentenceTransformers 6.1.0, Transformers 5.19.0, PyTorch 2.14.1+cpu, Chroma 1.5.9,
HF Hub 1.33.0, FastAPI 0.142.2, Uvicorn 0.54.0, pypdf 6.19.0, HTTPX 0.28.1.
Full versions, fixture/PDF/lock hashes, model artifact hashes, raw times and the measured driver
SHA-256 are recorded in JSON. Production code at measurement was `f36de97` (PR #3 based on
merged main `c85f325`); the additional measured driver is identified by its hash. Pinning the
default afterward selects the same explicitly configured revision used for this run.

From the repository root, activate the locked Python environment and prepare the official cache:

```bash
export HF_HOME="$PWD/data/models"
export HF_HUB_CACHE="$HF_HOME/huggingface"
export HF_XET_CACHE="$HF_HOME/xet"
python - <<'PY'
import json
from pathlib import Path
from huggingface_hub import HfApi, snapshot_download
from evaluation.real_retrieval import MODEL, REVISION
info = HfApi().model_info(MODEL, revision=REVISION, files_metadata=True, token=False)
assert info.sha == REVISION
files = list(json.loads(Path("docs/evaluation/multilingual-e5-synthetic-tender-3.json").read_text())["model"]["files"])
snapshot = snapshot_download(MODEL, revision=REVISION, allow_patterns=files, token=False)
Path("/tmp/e5-download.json").write_text(json.dumps({
    "id": MODEL, "revision": info.sha, "license": info.card_data.license,
    "snapshot": str(Path(snapshot).resolve()),
    "weight_lfs_sha256": next(f.lfs.sha256 for f in info.siblings if f.rfilename == "model.safetensors"),
}))
PY
HF_HUB_OFFLINE=1 python -m evaluation.real_retrieval --download-record /tmp/e5-download.json --data-dir /tmp/tendercite-e5-fresh-run --output /tmp/e5-measured-results.json
```

Use new/nonexistent data and output paths each time. The driver refuses to overwrite prior
measurements. Downloading uses the managed proxy with TLS; evaluation then loads the pinned
cache offline. It starts/stops only its own loopback server and leaves its isolated data for
inspection. CI only recalculates recorded metrics and runs existing fake-provider tests; it
never invokes this opt-in real-model driver or downloads weights.

## Validation

`HF_HUB_OFFLINE=1 pytest`: **65 passed**, none skipped (one existing upstream Starlette/httpx
deprecation warning). `ruff check .`, `ruff format --check .` and `docker compose config --quiet`
passed. New offline tests recalculate every reported query/context metric from the recorded
source chunks and hits, and verify artifact/provenance consistency.

## Remaining gates

This closes successful model loading/revision verification and the first synthetic retrieval
measurement. It does **not** establish real public-tender accuracy, exhaustive extraction,
robust multilingual/category coverage, or legal correctness. Category coverage failures need
review and a broader representative corpus before tuning or release claims. Real structured
LLM extraction/evidence evaluation remains the next phase; no LLM was configured or called.
Model/provider deployment checks, manual finding review and the existing security/license and
release checks remain. Old blocked records are historical, not current evaluation status.
