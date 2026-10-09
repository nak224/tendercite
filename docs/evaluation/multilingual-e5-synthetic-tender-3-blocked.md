# First multilingual E5 retrieval evaluation attempt

**Historical status: blocked before model loading.** Resolved on 2026-10-08; see the
[completed evaluation](multilingual-e5-synthetic-tender-3.md). The diagnostic JSON below preserves
the original failed attempt and is not the current result.
Attempted on 2026-10-08 from merged PR #2 / main
`c85f3254e8a665fd7a6aac76986aba751ce12201`, on branch `eval/multilingual-e5`.
[Machine-readable record](multilingual-e5-synthetic-tender-3-blocked.json) includes all 14
case queries/quotes, null measurements, configuration, dependency versions and diagnostics.
No external LLM was configured or called. No model weights or secrets are committed.

## Model metadata verified

The official Hugging Face API returned HTTP 200 and resolved `intfloat/multilingual-e5-small`
to immutable commit **`614241f622f53c4eeff9890bdc4f31cfecc418b3`**. A second request to the
[revision-specific metadata](https://huggingface.co/api/models/intfloat/multilingual-e5-small/revision/614241f622f53c4eeff9890bdc4f31cfecc418b3)
confirmed that SHA. The downloaded model card and API both declare **MIT**.

The model card and configuration specify 384-dimensional embeddings, 12 transformer layers,
12 attention heads, float32 weights, a BERT architecture with an XLM-R tokenizer, mean-token
pooling followed by normalization, and a 512-token maximum sequence length. The model card
requires `query: ` and `passage: ` prefixes even for non-English text. TenderCite's existing
provider supplies these prefixes and normalizes embeddings on CPU with remote code disabled.
Metadata-file SHA-256 hashes and the inspected configurations are retained in the JSON record.

**This is metadata verification, not successful model-load verification.** The weights and
large tokenizer files were not downloaded. SentenceTransformers loading and runtime dimensions
remain unverified. `TENDERCITE_EMBEDDING_REVISION` was therefore not pinned as a verified revision.

## Download blocker

| Operation | Observed result |
| --- | --- |
| Official Hugging Face model metadata/configuration | HTTP 200; small metadata files downloaded |
| Xet reconstruction at `cas-server.xethub.hf.co` | `httpx.ProxyError: 403 Forbidden` |
| Official HTTPS weight URL, redirected to `us.aws.cdn.hf.co` | `httpx.ProxyError: 403 Forbidden` |

The initial Xet attempt also encountered a read-only home cache. Writable `HF_HOME`,
`HF_HUB_CACHE` and `HF_XET_CACHE` under ignored `data/models/` fixed that filesystem problem;
the remaining failures are network proxy denials. TLS verification and the managed proxy were
retained. Signed redirect query strings and credentials are omitted from the diagnostic record.

A network configuration draft was saved with the required download hosts, preserving the known
GitHub/Hugging Face hosts: `api.github.com`, `huggingface.co`, `cas-bridge.xethub.hf.co`,
`cas-server.xethub.hf.co`, `us.aws.cdn.hf.co`. **The draft is not active:** review and save it in
environment settings, then publish the environment. Recheck access before retrying downloads;
additional upstream redirects, if any, must be diagnosed from their actual errors.

## Requested measurements

The unchanged `synthetic-tender-3` fixture has seven English and seven German cases, including
separate SECURITY and PRIVACY cases. Local PDF parsing with the normal 1,200-character chunks
and 150-character overlap produces **15 chunks from eight pages**. This is an ingestion check,
not an embedding retrieval result.

| Group | Cases | Page Hit@1 / @3 / @5 | Source-span Hit@1 / @3 / @5 |
| --- | ---: | --- | --- |
| English | 7 | Not run / not run / not run | Not run / not run / not run |
| German | 7 | Not run / not run / not run | Not run / not run / not run |
| Combined | 14 | Not run / not run / not run | Not run / not run / not run |

Per-case retrieval hits, ranks and latencies are **null**, not empty successful results or zero
scores. No cases can yet be classified as retrieval successes or failures.

The category-aware plan is recorded separately: strategy `category-aware-round-robin`, version
`1`, unchanged default user query plus six fixed bilingual queries, three hits per query and
**12 final context chunks maximum**. Its complete-source-quote coverage is unmeasured in every
language group. The previous broad-query baseline at the same budget is also unmeasured.
No extraction endpoint or LLM is needed to measure either context-selection strategy.

## Environment and performance

Python 3.12.14; SentenceTransformers 6.1.0; Transformers 5.19.0; PyTorch 2.14.1+cpu;
Chroma 1.5.9; Hugging Face Hub 1.33.0; FastAPI 0.142.2; pypdf 6.19.0; HTTPX 0.28.1.
CPU: AMD EPYC 9V74, x86-64, five visible/affinity CPUs, container quota equivalent to four CPUs
(`cpu.max = 400000 100000`), 32 GiB memory limit. Exact package versions, lockfile hash and
fixture hash are recorded in JSON. These describe the attempted environment, not a benchmark.

Embedding, indexing, query latency and analysis-plan latency are **not measured**. The failed
download's wall time is not presented as embedding performance.

## Offline validation

`HF_HUB_OFFLINE=1 pytest`: **63 passed**, none skipped; one existing upstream
Starlette/httpx deprecation warning. `ruff check .` and `ruff format --check .` passed.
These validate the existing software with test doubles; they are not real-model measurements.

## Resume and release gates

After applying network access, retry the official download using writable ignored cache paths:

```bash
export HF_HOME="$PWD/data/models"
export HF_HUB_CACHE="$HF_HOME/huggingface"
export HF_XET_CACHE="$HF_HOME/xet"
python - <<'PY'
from huggingface_hub import snapshot_download
snapshot_download(
    "intfloat/multilingual-e5-small",
    revision="614241f622f53c4eeff9890bdc4f31cfecc418b3",
    allow_patterns=[
        "README.md", "config.json", "modules.json", "sentence_bert_config.json",
        "1_Pooling/config.json", "model.safetensors", "sentencepiece.bpe.model",
        "special_tokens_map.json", "tokenizer.json", "tokenizer_config.json",
    ],
    token=False,
    max_workers=3,
)
PY
```

Then verify the exact revision loads in SentenceTransformers, confirm normalized 384-dimensional
query/passage vectors, and only then set `TENDERCITE_EMBEDDING_REVISION` to that tested SHA.
Use an isolated data directory and the real FastAPI upload/search routes. Retain per-case top-five
hits and calculate page/source-span rates at 1, 3 and 5 by language. Separately execute the existing
`build_analysis_retrieval_plan` / `retrieve_analysis_context` functions at budget 12, inspect final
chunk quotes, and compare with a single broad search at 12. Record timings without model-download
or cold-load time being silently included in warm retrieval latency. Keep the model revision,
query plan, dataset, raw hits, environment and trial configuration with the completed report.

Remaining release gates: verified model loading and revision pin; all real retrieval/context
coverage measurements; subsequent real structured-LLM extraction/evidence evaluation; manual
failure review; real model/provider deployment checks; and the existing audit/license/release
checks. Even a completed 14-case synthetic evaluation would not establish accuracy on real
public tenders or exhaustive extraction. This task does not advance those unmeasured gates.
