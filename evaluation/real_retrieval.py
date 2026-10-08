"""Opt-in measurement using real embeddings and loopback HTTP; never calls an LLM."""

import argparse
import hashlib
import importlib.metadata
import json
import math
import os
import platform
import socket
import statistics
import subprocess
import threading
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter, sleep

import httpx
import uvicorn

from evaluation.generate import make_pdf
from evaluation.metrics import retrieval_metrics
from tendercite.domain.analysis import AnalysisRequest
from tendercite.services.retrieval.analysis import (
    build_analysis_retrieval_plan,
    retrieve_analysis_context,
)
from tendercite.services.retrieval.base import SearchHit, SearchRequest

MODEL = "intfloat/multilingual-e5-small"
REVISION = "614241f622f53c4eeff9890bdc4f31cfecc418b3"


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def summaries(cases, results, document_id, ks):
    groups = {}
    for language in ("en", "de", "combined"):
        gold = [c for c in cases if language == "combined" or c["language"] == language]
        metrics = {"cases": len(gold)}
        for k in ks:
            measured = retrieval_metrics(gold, results, k, document_id)
            for name in ("page_hit", "source_span_hit"):
                metrics[f"{name}_at_{k}"] = measured[f"{name}_at_k"]
        groups[language] = metrics
    return groups


def compact(hits):
    return [{k: v for k, v in hit.items() if k != "text"} for hit in hits]


def distribution(samples):
    return {
        "count": len(samples),
        "min": min(samples),
        "median": statistics.median(samples),
        "mean": statistics.mean(samples),
        "max": max(samples),
    }


@contextmanager
def serve(app):
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        server = uvicorn.Server(uvicorn.Config(app, log_level="error", access_log=False))
        thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
        thread.start()
        try:
            for _ in range(100):
                if server.started:
                    break
                if not thread.is_alive():
                    raise RuntimeError("API failed to start")
                sleep(0.05)
            # Only loopback HTTP avoids proxy routing. HF keeps the managed HTTPS proxy/TLS.
            with httpx.Client(
                base_url=f"http://127.0.0.1:{listener.getsockname()[1]}",
                trust_env=False,
                timeout=180,
            ) as client:
                client.get("/health").raise_for_status()
                yield client
        finally:
            server.should_exit = True
            thread.join(timeout=10)
            if thread.is_alive():
                raise RuntimeError("API did not stop")


class HttpSearch:
    def __init__(self, client):
        self.client = client
        self.calls = []

    def search(self, request):
        started = perf_counter()
        response = self.client.post("/api/v1/search", json=request.model_dump())
        response.raise_for_status()
        hits = [SearchHit.model_validate(hit) for hit in response.json()]
        self.calls.append({"request": request.model_dump(), "seconds": perf_counter() - started})
        return hits


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, required=True, help="Must not already exist")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--download-record", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Preserve prior measurements: output already exists")
    download = json.loads(args.download_record.read_text())
    assert download["id"] == MODEL and download["revision"] == REVISION
    snapshot = Path(download["snapshot"])
    assert snapshot.name == REVISION
    assert digest(snapshot / "model.safetensors") == download["weight_lfs_sha256"]
    args.data_dir.mkdir(parents=True, exist_ok=False)
    os.environ.update(
        TENDERCITE_DATA_DIR=str(args.data_dir.resolve()),
        TENDERCITE_EMBEDDING_MODEL=MODEL,
        TENDERCITE_EMBEDDING_REVISION=REVISION,
        TENDERCITE_RETRIEVAL_ENABLED="true",
        TENDERCITE_LLM_BASE_URL="",
        TENDERCITE_LLM_MODEL="",
        OMP_NUM_THREADS="4",
        MKL_NUM_THREADS="4",
        TOKENIZERS_PARALLELISM="false",
    )
    # Settings must be initialized after the isolated evaluation configuration above.
    import torch

    from tendercite.api.dependencies import get_retrieval
    from tendercite.main import app

    torch.set_num_threads(4)
    torch.set_num_interop_threads(1)
    service = get_retrieval()
    started = perf_counter()
    probe = service.embeddings.embed_query("Welche Angebotsfrist gilt?")
    load_seconds = perf_counter() - started
    assert len(probe) == 384 and math.isclose(sum(v * v for v in probe), 1, abs_tol=1e-5)
    model = service.embeddings._model
    assert service.embeddings.revision == REVISION
    assert model.get_sentence_embedding_dimension() == 384
    timings = {"cached_model_load_and_first_query": load_seconds, "embedding": [], "indexing": []}
    original_embed, original_index = service.embeddings.embed_documents, service.index_document

    def timed_embed(texts):
        started = perf_counter()
        vectors = original_embed(texts)
        timings["embedding"].append({"chunks": len(texts), "seconds": perf_counter() - started})
        assert all(
            len(v) == 384 and math.isclose(sum(x * x for x in v), 1, abs_tol=1e-5) for v in vectors
        )
        return vectors

    def timed_index(document_id):
        started = perf_counter()
        count = original_index(document_id)
        timings["indexing"].append({"chunks": count, "seconds": perf_counter() - started})
        return count

    service.embeddings.embed_documents, service.index_document = timed_embed, timed_index
    gold_path = Path(__file__).with_name("gold.json")
    gold = json.loads(gold_path.read_text())
    assert gold["dataset_version"] == "synthetic-tender-3" and len(gold["cases"]) == 14
    pdf = make_pdf(gold["pages"])
    with serve(app) as client:
        configuration = client.get("/api/v1/configuration").json()
        assert not configuration["llm_configured"]
        started = perf_counter()
        response = client.post(
            "/api/v1/documents", files={"file": ("gold.pdf", pdf, "application/pdf")}
        )
        response.raise_for_status()
        timings["upload_parse_chunk_store_embed_index"] = perf_counter() - started
        document = response.json()
        response = client.get(f"/api/v1/documents/{document['id']}/chunks")
        response.raise_for_status()
        chunks = response.json()
        assert len(chunks) == document["chunk_count"] == service.store.collection.count() == 15
        request = AnalysisRequest(document_ids=[document["id"]], top_k=12)
        plan = build_analysis_retrieval_plan(request)
        search = HttpSearch(client)
        trials = []
        for trial in range(3):
            hits, per_case = {}, {}
            for case in gold["cases"]:
                hits[case["id"]] = [
                    h.model_dump()
                    for h in search.search(
                        SearchRequest(
                            query=case["query"], document_ids=request.document_ids, top_k=5
                        )
                    )
                ]
                per_case[case["id"]] = {
                    "hits": compact(hits[case["id"]]),
                    "seconds": search.calls[-1]["seconds"],
                }
            start_call = len(search.calls)
            started = perf_counter()
            context, audit = retrieve_analysis_context(plan, request.document_ids, search)
            plan_seconds = perf_counter() - started
            context = [h.model_dump() for h in context]
            plan_calls = search.calls[start_call:]
            baseline = [
                h.model_dump() for h in search.search(SearchRequest(**request.model_dump()))
            ]
            trials.append(
                {
                    "trial": trial + 1,
                    "query_metrics": summaries(gold["cases"], hits, document["id"], (1, 3, 5)),
                    "cases": per_case,
                    "analysis_plan": {
                        "seconds": plan_seconds,
                        "search_calls": plan_calls,
                        "query_results": [r.model_dump() for r in audit],
                        "context": compact(context),
                        "coverage_at_budget_12": summaries(
                            gold["cases"],
                            {c["id"]: context for c in gold["cases"]},
                            document["id"],
                            (12,),
                        ),
                    },
                    "single_query_baseline": {
                        "seconds": search.calls[-1]["seconds"],
                        "context": compact(baseline),
                        "coverage_at_budget_12": summaries(
                            gold["cases"],
                            {c["id"]: baseline for c in gold["cases"]},
                            document["id"],
                            (12,),
                        ),
                    },
                }
            )
            print(f"Completed real retrieval trial {trial + 1}/3", flush=True)
    timings["warm_query_http_top_5"] = distribution(
        [c["seconds"] for t in trials for c in t["cases"].values()]
    )
    timings["category_plan_http"] = distribution([t["analysis_plan"]["seconds"] for t in trials])
    timings["baseline_http_top_12"] = distribution(
        [t["single_query_baseline"]["seconds"] for t in trials]
    )
    tokens = [len(model.tokenizer.encode("passage: " + c["text"])) for c in chunks]
    record = {
        "status": "completed_real_retrieval",
        "created_at_utc": datetime.now(UTC).isoformat(),
        "code_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        "runner_sha256": digest(Path(__file__)),
        "lock_sha256": digest(Path("requirements-dev.lock")),
        "model": {
            "id": MODEL,
            "revision": REVISION,
            "license": download["license"],
            "dimension": len(probe),
            "normalized_vectors_verified": True,
            "load_revision": service.embeddings.revision,
            "max_seq_length": model.max_seq_length,
            "device": str(model.device),
            "dtype": str(next(model.parameters()).dtype),
            "trust_remote_code": False,
            "prefixes": {"query": "query: ", "passage": "passage: "},
            "configuration": json.loads((snapshot / "config.json").read_text()),
            "pooling": json.loads((snapshot / "1_Pooling/config.json").read_text()),
            "files": {
                str(p.relative_to(snapshot)): {"bytes": p.stat().st_size, "sha256": digest(p)}
                for p in sorted(snapshot.rglob("*"))
                if p.is_file()
            },
            "official_weight_lfs_sha256": download["weight_lfs_sha256"],
        },
        "dataset": {
            "version": gold["dataset_version"],
            "sha256": digest(gold_path),
            "pdf_sha256": hashlib.sha256(pdf).hexdigest(),
            "document": document,
            "gold": gold["cases"],
            "chunks": chunks,
            "chunk_size": 1200,
            "overlap": 150,
            "prefixed_passage_token_lengths": tokens,
            "truncated_passages": sum(n > model.max_seq_length for n in tokens),
        },
        "environment": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "cpu_model": next(
                line.split(":", 1)[1].strip()
                for line in Path("/proc/cpuinfo").read_text().splitlines()
                if line.startswith("model name")
            ),
            "affinity_cpus": len(os.sched_getaffinity(0)),
            "cpu_max": Path("/sys/fs/cgroup/cpu.max").read_text().strip(),
            "memory_max": Path("/sys/fs/cgroup/memory.max").read_text().strip(),
            "torch_threads": torch.get_num_threads(),
            "torch_interop_threads": torch.get_num_interop_threads(),
            "versions": {
                p: importlib.metadata.version(p)
                for p in [
                    "sentence-transformers",
                    "transformers",
                    "torch",
                    "huggingface-hub",
                    "chromadb",
                    "fastapi",
                    "uvicorn",
                    "pypdf",
                    "httpx",
                    "numpy",
                    "tokenizers",
                    "safetensors",
                ]
            },
        },
        "configuration": configuration,
        "analysis_request": request.model_dump(),
        "retrieval_plan": plan.model_dump(),
        "timings_seconds": timings,
        "trials": trials,
        "external_llm_called": False,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n")
    print(f"Wrote measured results to {args.output}", flush=True)


if __name__ == "__main__":
    main()
