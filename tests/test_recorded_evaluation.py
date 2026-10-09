"""Recalculate published metrics from recorded real hits, without loading a model."""

import json
from pathlib import Path

from evaluation.real_retrieval import REVISION, digest, summaries


def test_recorded_real_metrics_recalculate_from_source_chunks():
    record = json.loads(Path("docs/evaluation/multilingual-e5-synthetic-tender-3.json").read_text())
    dataset = record["dataset"]
    assert dataset["sha256"] == digest(Path("evaluation/gold.json"))
    assert dataset["gold"] == json.loads(Path("evaluation/gold.json").read_text())["cases"]
    assert record["runner_sha256"] == digest(Path("evaluation/real_retrieval.py"))
    assert record["lock_sha256"] == digest(Path("requirements-dev.lock"))
    chunks = {c["id"]: c for c in dataset["chunks"]}

    def restore(hits):
        for hit in hits:
            chunk = chunks[hit["chunk_id"]]
            assert hit["document_id"] == chunk["document_id"] == dataset["document"]["id"]
            assert hit["page_number"] == chunk["page_number"]
        return [{**hit, "text": chunks[hit["chunk_id"]]["text"]} for hit in hits]

    for trial in record["trials"]:
        results = {case: restore(result["hits"]) for case, result in trial["cases"].items()}
        assert trial["query_metrics"] == summaries(
            dataset["gold"], results, dataset["document"]["id"], (1, 3, 5)
        )
        for strategy in ("analysis_plan", "single_query_baseline"):
            context = restore(trial[strategy]["context"])
            assert len({h["chunk_id"] for h in context}) == len(context) <= 12
            assert trial[strategy]["coverage_at_budget_12"] == summaries(
                dataset["gold"],
                {c["id"]: context for c in dataset["gold"]},
                dataset["document"]["id"],
                (12,),
            )
        candidates = {
            chunk_id
            for query in trial["analysis_plan"]["query_results"]
            for chunk_id in query["chunk_ids"]
        }
        assert {h["chunk_id"] for h in trial["analysis_plan"]["context"]} <= candidates


def test_recorded_model_and_run_provenance_are_explicit():
    record = json.loads(Path("docs/evaluation/multilingual-e5-synthetic-tender-3.json").read_text())
    assert record["status"] == "completed_real_retrieval"
    assert record["model"]["revision"] == record["model"]["load_revision"] == REVISION
    assert (
        record["model"]["files"]["model.safetensors"]["sha256"]
        == record["model"]["official_weight_lfs_sha256"]
    )
    assert record["model"]["dimension"] == 384
    assert record["model"]["normalized_vectors_verified"]
    assert record["external_llm_called"] is False
    assert record["configuration"]["llm_configured"] is False
    assert record["analysis_request"]["top_k"] == record["retrieval_plan"]["max_chunks"] == 12
    assert len(record["trials"]) == 3
    assert record["timings_seconds"]["warm_query_http_top_5"]["count"] == 42
