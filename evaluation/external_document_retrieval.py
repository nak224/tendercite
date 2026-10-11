"""Opt-in real E5 document retrieval; all raw outputs stay in ignored evaluation storage."""

import argparse
import hashlib
import importlib.metadata
import json
import logging
import math
import os
import platform
import statistics
import subprocess
import uuid
from pathlib import Path
from time import perf_counter

from evaluation.document_retrieval_metrics import evaluate_cases
from evaluation.external_benchmark import confined, failure_details, read_artifact
from evaluation.external_document_cases import prepare_cases
from evaluation.ted_acquisition import timestamp
from evaluation.validate_annotations import write_json
from tendercite.domain.models import ChunkRead, PageRead
from tendercite.repositories.sqlite import SqliteRepository
from tendercite.services.chunking import chunk_pages
from tendercite.services.pdf_parser import PyPdfParser
from tendercite.services.retrieval.embeddings import SentenceTransformerEmbeddingProvider
from tendercite.services.retrieval.service import RetrievalService

MODEL = "intfloat/multilingual-e5-small"
MODEL_REVISION = "614241f622f53c4eeff9890bdc4f31cfecc418b3"
DOCUMENT_KS = (1, 2, 3)
CHUNK_TOP_K = 50


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def load_model(cache: Path):
    """Official public SDK and immutable local snapshot; retain inherited TLS/proxy trust."""
    cache = confined(cache)
    os.environ["HF_XET_CACHE"] = str(cache / ".xet-cache")
    from huggingface_hub import HfApi, constants, snapshot_download

    confined(Path(constants.HF_XET_CACHE))

    started = perf_counter()
    info = HfApi(token=False).model_info(
        MODEL, revision=MODEL_REVISION, files_metadata=True, timeout=20
    )
    if info.sha != MODEL_REVISION:
        raise ValueError("Official model metadata returned a different revision")
    weight = next((file for file in info.siblings if file.rfilename == "model.safetensors"), None)
    if weight is None or not weight.lfs or not weight.lfs.sha256:
        raise ValueError("Official model weight checksum unavailable")
    cached = cache / "models--intfloat--multilingual-e5-small/snapshots" / MODEL_REVISION
    cache_was_present = (cached / "model.safetensors").is_file()
    snapshot = Path(
        snapshot_download(
            MODEL,
            revision=MODEL_REVISION,
            cache_dir=cache,
            token=False,
            allow_patterns=["*.json", "*.safetensors", "*.model", "*.txt", "README.md"],
        )
    )
    if snapshot.name != MODEL_REVISION or not snapshot.resolve().is_relative_to(cache.resolve()):
        raise ValueError("Model snapshot does not match the isolated pinned cache")
    if sha256(snapshot / "model.safetensors") != weight.lfs.sha256:
        raise ValueError("Model weight checksum mismatch")
    download_seconds = perf_counter() - started
    from sentence_transformers import SentenceTransformer

    embeddings = SentenceTransformerEmbeddingProvider(MODEL, MODEL_REVISION)
    started = perf_counter()
    # The snapshot was downloaded and verified above; load only that immutable directory.
    embeddings._model = SentenceTransformer(
        str(snapshot), device="cpu", local_files_only=True, trust_remote_code=False
    )
    vector = embeddings.embed_query("public procurement software requirements")
    if len(vector) != 384 or not math.isclose(sum(x * x for x in vector), 1, abs_tol=1e-5):
        raise ValueError("Expected a normalized 384-dimensional multilingual E5 embedding")
    return embeddings, {
        "id": MODEL,
        "revision": MODEL_REVISION,
        "status": "official_snapshot_downloaded_and_loaded",
        "weight_sha256": weight.lfs.sha256,
        "weight_cache_present_before_run": cache_was_present,
        "download_metadata_and_hash_seconds": download_seconds,
        "load_and_probe_seconds": perf_counter() - started,
        "dimension": len(vector),
        "normalized_query_verified": True,
        "max_seq_length": embeddings._model.max_seq_length,
        "device": str(embeddings._model.device),
        "dtype": str(next(embeddings._model.parameters()).dtype),
        "prefixes": {"query": "query: ", "passage": "passage: "},
        "trust_remote_code": False,
    }


def index_documents(prepared, directory: Path, embeddings, store):
    """Use the production parser/chunker/repository/index service without global API settings."""
    repository = SqliteRepository(directory / "tendercite.db")
    retrieval = RetrievalService(repository, embeddings, store)
    mapping, documents = {}, []
    inventory = {record["filename"]: record for record in prepared.manifest["inventory"]}
    for name, record in prepared.documents.items():
        started = perf_counter()
        read_artifact(record["local_path"], inventory[record["filename"]], record)
        pages = PyPdfParser().parse(record["local_path"], max_pages=250, max_chars=1_000_000)
        chunks = chunk_pages([(page.page_number, page.text) for page in pages])
        if not chunks:
            raise ValueError(f"No searchable chunks: {name}")
        doc_id = str(uuid.uuid5(uuid.NAMESPACE_URL, "external-pdf-sha256:" + record["sha256"]))
        document = repository.save_document(
            document_id=doc_id,
            filename=name,
            media_type="application/pdf",
            sha256=record["sha256"],
            pages=[
                PageRead(document_id=doc_id, page_number=page.page_number, text=page.text)
                for page in pages
            ],
            chunks=[
                ChunkRead(
                    id=str(uuid.uuid5(uuid.UUID(doc_id), f"{chunk.page_number}:{chunk.ordinal}")),
                    document_id=doc_id,
                    page_number=chunk.page_number,
                    ordinal=chunk.ordinal,
                    text=chunk.text,
                    char_start=chunk.char_start,
                    char_end=chunk.char_end,
                )
                for chunk in chunks
            ],
        )
        mapping[name] = document.id
        parsed_seconds = perf_counter() - started
        started = perf_counter()
        count = retrieval.index_document(document.id)
        documents.append(
            {
                "upstream_filename": name,
                "document_id": document.id,
                "sha256": document.sha256,
                "pages": document.page_count,
                "chunks": count,
                "parse_chunk_persist_seconds": parsed_seconds,
                "embed_and_index_seconds": perf_counter() - started,
            }
        )
    return retrieval, mapping, documents


def run(acquisition_dir: Path, output_dir: Path, model_cache: Path) -> dict:
    output_dir = confined(output_dir)
    output_dir.mkdir(parents=True, exist_ok=False)
    stage, started = "case_preparation", perf_counter()
    record = {"status": "failed", "exploratory_candidate_labels": True, "llm_called": False}
    try:
        prepared = prepare_cases(acquisition_dir)
        record.update(
            {
                "dataset_revision": prepared.manifest["revision"],
                "family": prepared.manifest["family"],
                "eligible_cases": len(prepared.eligible),
                "exclusions": prepared.excluded,
                "dataset_artifacts": prepared.manifest["files"],
            }
        )
        if not prepared.eligible or not prepared.documents:
            raise ValueError("No eligible cases/searchable documents; no metrics calculated")
        stage = "model_download_or_loading"
        os.environ["TOKENIZERS_PARALLELISM"] = "false"
        import torch

        torch.set_num_threads(4)
        torch.set_num_interop_threads(1)
        embeddings, model_record = load_model(model_cache)
        record["model"] = model_record
        original_embed = embeddings.embed_documents
        verified_passages = 0

        def verified_embed(texts):
            nonlocal verified_passages
            vectors = original_embed(texts)
            if len(vectors) != len(texts) or any(
                len(v) != 384 or not math.isclose(sum(x * x for x in v), 1, abs_tol=1e-5)
                for v in vectors
            ):
                raise ValueError("Expected normalized 384-dimensional E5 passage vectors")
            verified_passages += len(vectors)
            return vectors

        embeddings.embed_documents = verified_embed
        stage = "indexing"
        from tendercite.services.retrieval.chroma import ChromaVectorStore

        store = ChromaVectorStore(output_dir / "chroma", "external-document-evaluation")
        retrieval, mapping, documents = index_documents(prepared, output_dir, embeddings, store)
        model_record["normalized_indexed_passages_verified"] = verified_passages
        tokens = [
            len(embeddings._model.tokenizer.encode("passage: " + chunk.text))
            for doc_id in sorted(set(mapping.values()))
            for chunk in retrieval.repository.list_chunks(doc_id)
        ]
        stage = "retrieval"
        evaluated = evaluate_cases(
            prepared.eligible, mapping, retrieval, chunk_top_k=CHUNK_TOP_K, ks=DOCUMENT_KS
        )
        record.update(
            {
                "status": "completed_real_document_retrieval",
                "filename_document_id_mapping": mapping,
                "indexed_documents": documents,
                "configuration": {
                    "chunk_size_characters": 1200,
                    "overlap_characters": 150,
                    "embedding_batch_size": 64,
                    "similarity": "cosine",
                    "chunk_top_k": CHUNK_TOP_K,
                    "document_ks": list(DOCUMENT_KS),
                    "document_ranking": "first appearance in ranked chunk pool",
                    "search_document_ids": sorted(set(mapping.values())),
                    "max_prefixed_passage_tokens": max(tokens),
                    "passages_exceeding_model_token_limit": sum(
                        n > embeddings._model.max_seq_length for n in tokens
                    ),
                },
                **evaluated,
                "environment": {
                    "python": platform.python_version(),
                    "platform": platform.platform(),
                    "cpu": (
                        next(
                            (
                                line.split(":", 1)[1].strip()
                                for line in Path("/proc/cpuinfo").read_text().splitlines()
                                if line.startswith("model name")
                            ),
                            platform.processor(),
                        )
                        if Path("/proc/cpuinfo").is_file()
                        else platform.processor()
                    ),
                    "cpu_quota": Path("/sys/fs/cgroup/cpu.max").read_text().strip()
                    if Path("/sys/fs/cgroup/cpu.max").is_file()
                    else None,
                    "cpu_count": os.cpu_count(),
                    "cpu_affinity_count": len(os.sched_getaffinity(0))
                    if hasattr(os, "sched_getaffinity")
                    else None,
                    "torch_threads": torch.get_num_threads(),
                    "torch_interop_threads": torch.get_num_interop_threads(),
                    "versions": {
                        name: importlib.metadata.version(name)
                        for name in (
                            "sentence-transformers",
                            "transformers",
                            "huggingface-hub",
                            "torch",
                            "chromadb",
                            "pypdf",
                            "numpy",
                        )
                    },
                },
                "mean_query_seconds": statistics.mean(
                    c["search_seconds"] for c in evaluated["cases"]
                ),
                "code_commit": subprocess.check_output(
                    ["git", "rev-parse", "HEAD"], text=True
                ).strip(),
                "runner_sha256": sha256(Path(__file__)),
                "evaluation_sources_sha256": {
                    name: sha256(Path(__file__).with_name(name))
                    for name in (
                        "external_document_cases.py",
                        "document_retrieval_metrics.py",
                        "external_document_retrieval.py",
                    )
                },
                "code_worktree_status": subprocess.check_output(
                    ["git", "status", "--porcelain"], text=True
                ).strip(),
                "lock_sha256": sha256(
                    Path(__file__).resolve().parents[1] / "requirements-dev.lock"
                ),
            }
        )
    except Exception as exc:
        record.update({"failed_stage": stage, "failure": failure_details(exc)})
    record.update({"recorded_at": timestamp(), "total_seconds": perf_counter() - started})
    write_json(output_dir / "results.json", record)
    return record


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--acquisition-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True, help="Fresh ignored directory")
    parser.add_argument("--model-cache", type=Path, required=True, help="Ignored external cache")
    parser.add_argument("--run-real-model", action="store_true", help="Explicitly allow HF access")
    args = parser.parse_args(argv)
    if not args.run_real_model:
        parser.error("Opt in with --run-real-model; normal CI must not download models")
    logging.basicConfig(level=logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    try:
        record = run(args.acquisition_dir, args.output_dir, args.model_cache)
    except (OSError, ValueError) as exc:
        logging.error("Evaluation not started: %s", exc)
        return 1
    print(f"{record['status']}: {record.get('eligible_cases', 0)} eligible cases")
    if record["status"] != "completed_real_document_retrieval":
        print(json.dumps(record.get("failure")))
        return 1
    print(json.dumps(record["metrics"], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
