"""Recorded-run verification with original PDFs and wholly synthetic reviewer declarations."""

import builtins
import hashlib
import json
import uuid

import pytest
from test_external_benchmark import FAMILY, REVISION
from test_external_document_metrics import corpus  # noqa: F401
from test_external_qa_spans import context, filled  # noqa: F401

from evaluation import external_source_span_retrieval as runner
from evaluation.external_qa_spans import load_review_context
from evaluation.validate_external_qa_spans import validate_review
from tendercite.services.chunking import chunk_pages
from tendercite.services.pdf_parser import PyPdfParser
from tendercite.services.retrieval.base import SearchHit


def write(path, value):
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def seal(inputs):
    """Rebind ONLY generated test artifacts after a deliberate synthetic results mutation."""
    ctx = load_review_context(
        inputs["acquisition"], inputs["retrieval"], family=FAMILY, revision=REVISION
    )
    payload = json.loads(inputs["review"].read_text())
    payload.update(ctx.header)
    write(inputs["review"], payload)
    report = validate_review(payload, ctx)
    assert report["status"] == "COMPLETE"
    report["annotations_sha256"] = hashlib.sha256(inputs["review"].read_bytes()).hexdigest()
    write(inputs["validation"], report)


@pytest.fixture
def inputs(context):  # noqa: F811 - imported pytest fixture supplies original generated PDFs.
    root = context.acquisition_dir.parent
    paths = {
        "acquisition": context.acquisition_dir,
        "retrieval": root / "synthetic-results.json",
        "review": root / "synthetic-review.json",
        "validation": root / "synthetic-complete.json",
    }
    recorded = json.loads(paths["retrieval"].read_text())
    mapping, documents, hits = {}, [], []
    for name, doc in context.documents.items():
        doc_id = str(uuid.uuid5(uuid.NAMESPACE_URL, "external-pdf-sha256:" + doc["sha256"]))
        pages = PyPdfParser().parse(doc["local_path"], max_pages=250, max_chars=1_000_000)
        chunks = chunk_pages([(p.page_number, p.text) for p in pages])
        mapping[name] = doc_id
        documents.append(
            {
                "upstream_filename": name,
                "document_id": doc_id,
                "sha256": doc["sha256"],
                "pages": len(pages),
                "chunks": len(chunks),
            }
        )
        hits.extend(
            SearchHit(
                chunk_id=str(uuid.uuid5(uuid.UUID(doc_id), f"{c.page_number}:{c.ordinal}")),
                document_id=doc_id,
                document_name=name,
                page_number=c.page_number,
                text=c.text,
                score=0.9,
            ).model_dump()
            for c in chunks
        )
    recorded.update(
        {
            # This is fixture metadata, not a claim of a live model call or measured benchmark.
            "model": {
                "id": runner.MODEL,
                "revision": runner.MODEL_REVISION,
                "status": "official_snapshot_downloaded_and_loaded",
                "dimension": 384,
            },
            "filename_document_id_mapping": mapping,
            "indexed_documents": documents,
            "configuration": {
                "chunk_size_characters": 1200,
                "overlap_characters": 150,
                "chunk_top_k": 50,
                "search_document_ids": sorted(mapping.values()),
            },
        }
    )
    for case in recorded["cases"]:
        case.update(
            {
                "expected_document_ids": [
                    mapping[name] for name in case["expected_source_documents"]
                ],
                "retrieved_chunks": hits,
                "retrieved_chunk_count": len(hits),
            }
        )
    write(paths["retrieval"], recorded)
    write(paths["review"], filled(context))
    seal(paths)
    return paths


def evaluate(inputs):
    return runner.evaluate_inputs(
        inputs["acquisition"],
        inputs["review"],
        inputs["validation"],
        inputs["retrieval"],
        family=FAMILY,
        revision=REVISION,
        expected_cases=2,
    )


def test_original_fixture_run_is_verified_without_network_or_models(inputs, monkeypatch):
    original_import = builtins.__import__

    def guarded_import(name, *args, **kwargs):
        if name.split(".")[0] in {
            "torch",
            "transformers",
            "sentence_transformers",
            "huggingface_hub",
        }:
            pytest.fail("Offline recorded scoring must not import models or download SDKs")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    snapshots = {name: path.read_bytes() for name, path in inputs.items() if path.is_file()}
    result = evaluate(inputs)
    assert result["status"] == "completed_recorded_source_span_evaluation"
    assert result["structural_summary"]["complete"] == 3
    assert result["metrics"]["combined"]["strict"]["source_span_hit_at_k"]["50"] == 1
    assert all(result["input_sha256"].values())
    assert result["cases"][1]["expected_document_ids"] == [
        result["filename_document_id_mapping"]["notice.pdf"],
        result["filename_document_id_mapping"]["contract.pdf"],
    ]
    assert all(
        path.read_bytes() == snapshots[name] for name, path in inputs.items() if path.is_file()
    )


@pytest.mark.parametrize(
    "mutation", ["review_hash", "status", "summary", "quote", "reviewer", "question_id"]
)
def test_incorrect_annotations_or_stale_complete_report_reject_scores(inputs, mutation):
    review = json.loads(inputs["review"].read_text())
    report = json.loads(inputs["validation"].read_text())
    if mutation == "review_hash":
        review["questions"][0]["annotation_notes"] = "Changed after validation"
    elif mutation == "status":
        report["status"] = "INCOMPLETE"
    elif mutation == "summary":
        report["evidence_complete_questions"] = 1
    elif mutation == "quote":
        review["questions"][0]["evidence"][0]["exact_source_quote"] = "Invented synthetic quote"
    elif mutation == "reviewer":
        review["questions"][0]["evidence"][0]["reviewer_alias"] = "synthetic-author"
    else:
        review["questions"][0]["question_id"] = "wrong-case"
    write(inputs["review"], review)
    if mutation != "review_hash":
        report["annotations_sha256"] = hashlib.sha256(inputs["review"].read_bytes()).hexdigest()
    write(inputs["validation"], report)
    with pytest.raises(ValueError, match="SHA-256|COMPLETE|stale"):
        evaluate(inputs)


@pytest.mark.parametrize("artifact", ["review", "validation", "retrieval", "manifest", "pdf"])
def test_missing_external_artifacts_fail_explicitly(inputs, artifact):
    if artifact == "manifest":
        path = inputs["acquisition"] / "manifest.json"
    elif artifact == "pdf":
        path = inputs["acquisition"] / f"artifacts/data/{FAMILY}/notice.pdf"
    else:
        path = inputs[artifact]
    path.unlink()
    with pytest.raises((OSError, ValueError)):
        evaluate(inputs)


@pytest.mark.parametrize(
    "mutation", ["model", "mapping", "page", "text", "chunk_id", "config", "scope", "index_hash"]
)
def test_inconsistent_recorded_e5_or_chunk_provenance_is_rejected(inputs, mutation):
    results = json.loads(inputs["retrieval"].read_text())
    if mutation == "model":
        results["model"]["revision"] = "b" * 40
    elif mutation == "mapping":
        results["filename_document_id_mapping"]["notice.pdf"] = "wrong-document-id"
    elif mutation in {"page", "text", "chunk_id"}:
        key = {"page": "page_number", "text": "text", "chunk_id": "chunk_id"}[mutation]
        results["cases"][0]["retrieved_chunks"][0][key] = 99 if mutation == "page" else "wrong"
    elif mutation == "config":
        results["configuration"]["chunk_size_characters"] = 2048
    elif mutation == "scope":
        results["configuration"]["search_document_ids"].append("outside-family-id")
    else:
        results["indexed_documents"][0]["sha256"] = "b" * 64
    write(inputs["retrieval"], results)
    seal(inputs)
    with pytest.raises(ValueError, match="Recorded|recorded"):
        evaluate(inputs)


def test_empty_and_duplicate_recorded_hits_are_supported_without_span_inflation(inputs):
    results = json.loads(inputs["retrieval"].read_text())
    results["cases"][0].update(retrieved_chunks=[], retrieved_chunk_count=0)
    duplicate = results["cases"][1]["retrieved_chunks"][0]
    results["cases"][1]["retrieved_chunks"].insert(0, duplicate)
    results["cases"][1]["retrieved_chunk_count"] += 1
    write(inputs["retrieval"], results)
    seal(inputs)
    output = evaluate(inputs)
    assert output["cases"][0]["strict"]["source_span_hit_at_k"]["50"] is False
    assert output["cases"][1]["duplicate_chunk_hits"] == 1
    assert output["cases"][1]["strict"]["verified_span_count"] == 2
    assert output["cases"][1]["strict"]["verified_span_recall_at_k"]["50"] == 1


def test_tampered_pdf_cannot_be_scored_using_stale_review_and_recorded_text(inputs):
    path = inputs["acquisition"] / f"artifacts/data/{FAMILY}/notice.pdf"
    path.write_bytes(b"tampered")
    with pytest.raises(ValueError):
        evaluate(inputs)


def test_default_cohort_requires_all_nine_original_eligible_questions(inputs):
    with pytest.raises(ValueError, match="cohort"):
        runner.evaluate_inputs(
            inputs["acquisition"],
            inputs["review"],
            inputs["validation"],
            inputs["retrieval"],
            family=FAMILY,
            revision=REVISION,
        )


def test_cli_records_blocker_without_scores_and_preserves_existing_outputs(inputs):
    inputs["validation"].unlink()
    output = inputs["review"].parent / "blocked"
    args = [
        "--acquisition-dir",
        str(inputs["acquisition"]),
        "--annotations",
        str(inputs["review"]),
        "--validation-report",
        str(inputs["validation"]),
        "--retrieval-results",
        str(inputs["retrieval"]),
        "--output-dir",
        str(output),
    ]
    assert runner.main(args) == 1
    path = output / "results.json"
    record = json.loads(path.read_text())
    assert record["status"] == "blocked"
    assert "metrics" not in record and "cases" not in record
    assert not record["embeddings_called"] and not record["llm_called"]
    assert "synthetic-complete.json" in record["failure"]
    original = path.read_bytes()
    assert runner.main(args) == 1
    assert path.read_bytes() == original


def test_successful_cli_saves_local_metric_results_with_input_hashes(inputs, monkeypatch):
    original = runner.evaluate_inputs
    monkeypatch.setattr(
        runner,
        "evaluate_inputs",
        lambda *args: original(
            *args,
            family=FAMILY,
            revision=REVISION,
            expected_cases=2,
        ),
    )
    output = inputs["review"].parent / "scored"
    assert (
        runner.main(
            [
                "--acquisition-dir",
                str(inputs["acquisition"]),
                "--annotations",
                str(inputs["review"]),
                "--validation-report",
                str(inputs["validation"]),
                "--retrieval-results",
                str(inputs["retrieval"]),
                "--output-dir",
                str(output),
            ]
        )
        == 0
    )
    record = json.loads((output / "results.json").read_text())
    assert (
        record["input_sha256"]["review"]
        == hashlib.sha256(inputs["review"].read_bytes()).hexdigest()
    )
    assert record["source_sha256"] and not record["embeddings_called"]
