"""Offline scoring of recorded E5 chunks; never load embeddings or alter human reviews."""

import argparse
import hashlib
import importlib.metadata
import json
import logging
import math
import platform
import tempfile
import uuid
from dataclasses import asdict
from pathlib import Path
from time import perf_counter

from pydantic import ValidationError

from evaluation import external_benchmark as acquisition
from evaluation.external_document_cases import DATASET_REVISION, FAMILY
from evaluation.external_qa_spans import load_review_context, read_local_json, write_new_json
from evaluation.external_source_span_metrics import (
    KS,
    VerifiedSpan,
    aggregate,
    case_metrics,
    scorable_metrics,
)
from evaluation.source_span_structure import diagnose_span, summarize_structure
from evaluation.ted_acquisition import timestamp
from evaluation.validate_external_qa_spans import validate_review
from tendercite.services.chunking import chunk_pages
from tendercite.services.pdf_parser import PyPdfParser
from tendercite.services.retrieval.base import SearchHit

MODEL = "intfloat/multilingual-e5-small"
MODEL_REVISION = "614241f622f53c4eeff9890bdc4f31cfecc418b3"


def rebuild_chunks(context, results):
    """Reconstruct the full recorded index from verified PDF snapshots, without embedding."""
    configuration = results["configuration"]
    if not isinstance(configuration, dict):
        raise ValueError("Recorded retrieval configuration must be an object")
    required_config = {"chunk_size_characters": 1200, "overlap_characters": 150, "chunk_top_k": 50}
    if any(
        type(configuration.get(k)) is not int or configuration[k] != v
        for k, v in required_config.items()
    ):
        raise ValueError(
            "Recorded chunk configuration differs from the supported production defaults"
        )
    mapping = {
        name: str(uuid.uuid5(uuid.NAMESPACE_URL, "external-pdf-sha256:" + doc["sha256"]))
        for name, doc in context.documents.items()
    }
    if results.get("filename_document_id_mapping") != mapping or configuration.get(
        "search_document_ids"
    ) != sorted(set(mapping.values())):
        raise ValueError(
            "Recorded filename/document ID mapping or family search scope is inconsistent"
        )
    recorded = results["indexed_documents"]
    if not isinstance(recorded, list) or not all(isinstance(doc, dict) for doc in recorded):
        raise ValueError("Recorded indexed documents must be an array of objects")
    details = {doc["upstream_filename"]: doc for doc in recorded}
    if len(details) != len(recorded) or set(details) != set(mapping):
        raise ValueError("Recorded indexed documents differ from the parseable family documents")
    manifest, _ = read_local_json(context.acquisition_dir / "manifest.json")
    inventory = {item["filename"]: item for item in manifest["inventory"]}
    pages_by_doc, indexed = {}, {}
    for name, document in context.documents.items():
        raw = acquisition.read_artifact(
            document["local_path"], inventory[document["filename"]], document
        )
        with tempfile.TemporaryDirectory(dir=context.acquisition_dir, prefix=".span-index-") as tmp:
            snapshot = Path(tmp) / "snapshot.pdf"
            snapshot.write_bytes(raw)
            pages = PyPdfParser().parse(snapshot, max_pages=250, max_chars=1_000_000)
        chunks = chunk_pages([(p.page_number, p.text) for p in pages])
        doc_id = mapping[name]
        expected = {
            "document_id": doc_id,
            "sha256": document["sha256"],
            "pages": len(pages),
            "chunks": len(chunks),
        }
        if any(details[name].get(k) != v for k, v in expected.items()):
            raise ValueError("Recorded indexed PDF hash, ID, page/chunk counts are inconsistent")
        pages_by_doc[doc_id] = {p.page_number: p.text for p in pages}
        for chunk in chunks:
            chunk_id = str(uuid.uuid5(uuid.UUID(doc_id), f"{chunk.page_number}:{chunk.ordinal}"))
            indexed[chunk_id] = SearchHit(
                chunk_id=chunk_id,
                document_id=doc_id,
                document_name=name,
                page_number=chunk.page_number,
                text=chunk.text,
                score=0,
            )
    return mapping, pages_by_doc, indexed


def checked_hits(case, indexed):
    raw = case["retrieved_chunks"]
    if not isinstance(raw, list) or len(raw) > 50 or case.get("retrieved_chunk_count") != len(raw):
        raise ValueError("Recorded chunk count/limit is inconsistent")
    hits = []
    for entry in raw:
        try:
            hit = SearchHit.model_validate(entry, strict=True)
        except ValidationError:
            raise ValueError("Malformed recorded retrieval chunk") from None
        source = indexed.get(hit.chunk_id)
        if source is None or hit.model_dump(exclude={"score"}) != source.model_dump(
            exclude={"score"}
        ):
            raise ValueError(
                "Recorded chunk ID/document/page/text differs from reconstructed index"
            )
        if not math.isfinite(hit.score) or (hits and hit.score > hits[-1].score):
            raise ValueError(
                "Recorded similarity scores must be finite and ranked in descending order"
            )
        hits.append(hit)
    return hits


def evaluate_inputs(
    acquisition_dir: Path,
    annotations: Path,
    validation: Path,
    retrieval_results: Path,
    *,
    family=FAMILY,
    revision=DATASET_REVISION,
    expected_cases=9,
) -> dict:
    payload, review_hash = read_local_json(annotations)
    saved, validation_hash = read_local_json(validation)
    results, retrieval_hash = read_local_json(retrieval_results)
    if not isinstance(saved, dict) or saved.get("status") != "COMPLETE":
        raise ValueError("Saved source-span validation must be COMPLETE; no scores calculated")
    if saved.get("annotations_sha256") != review_hash:
        raise ValueError("Review JSON does not match the saved validation report's SHA-256")
    context = load_review_context(
        acquisition_dir, retrieval_results, family=family, revision=revision
    )
    if len(context.questions) != expected_cases:
        raise ValueError("Eligible question count differs from the required evaluation cohort")
    fresh = validate_review(payload, context)
    if fresh["status"] != "COMPLETE":
        raise ValueError(
            f"Fresh annotation/provenance validation is {fresh['status']}, not COMPLETE"
        )
    if saved != {**fresh, "annotations_sha256": review_hash}:
        raise ValueError(
            "Saved COMPLETE validation report is stale or inconsistent with fresh validation"
        )
    model = results["model"]
    if not isinstance(model, dict):
        raise ValueError("Recorded model metadata must be an object")
    if (model.get("id"), model.get("revision"), model.get("status"), model.get("dimension")) != (
        MODEL,
        MODEL_REVISION,
        "official_snapshot_downloaded_and_loaded",
        384,
    ):
        raise ValueError(
            "Recorded model identity/revision/verification status differs from pinned E5"
        )
    mapping, pages, indexed = rebuild_chunks(context, results)
    recorded_cases = {case["id"]: case for case in results["cases"]}
    output, diagnostics = [], []
    for question in fresh["questions"]:
        qid = question["question_id"]
        original = context.questions[qid]
        recorded = recorded_cases[qid]
        required = [mapping[ref.filename] for ref in original.source_documents]
        if recorded.get("expected_document_ids") != required:
            raise ValueError(
                "Recorded expected document IDs do not match original source references"
            )
        hits = checked_hits(recorded, indexed)
        indices = [
            e["input_index"] for e in question["evidence_results"] if e["independently_verified"]
        ]
        spans = [
            VerifiedSpan(
                span_id=f"{qid}:evidence:{index}",
                source_document=e["source_document"],
                document_id=mapping[e["source_document"]],
                pdf_sha256=e["pdf_sha256"],
                page_number=e["page_number"],
                exact_source_quote=e["exact_source_quote"],
            )
            for index, e in zip(indices, question["verified_source_spans"], strict=True)
        ]
        structure = [
            diagnose_span(s, pages[s.document_id].get(s.page_number), list(indexed.values()))
            for s in spans
        ]
        if any(d["status"] == "mapping_inconsistency" for d in structure):
            raise ValueError("Verified span/index mapping inconsistency; no scores calculated")
        strict = case_metrics(spans, required, hits)
        scorable = [s for s, d in zip(spans, structure, strict=True) if d["status"] == "complete"]
        errors = []
        for span, diagnostic in zip(spans, structure, strict=True):
            rank = strict["first_matching_chunk_rank_by_span"][span.span_id]
            miss_reason = (
                "not_in_recorded_top_50_chunk_pool"
                if diagnostic["status"] == "complete"
                else diagnostic["status"]
            )
            errors.append(
                {
                    **asdict(span),
                    **diagnostic,
                    "first_matching_chunk_rank": rank,
                    "retrieved_at_k": {str(k): rank is not None and rank <= k for k in KS},
                    "miss_reason": None if rank else miss_reason,
                    "miss_reason_at_k": {
                        str(k): None
                        if rank and rank <= k
                        else ("below_requested_cutoff" if rank else miss_reason)
                        for k in KS
                    },
                }
            )
        output.append(
            {
                "id": qid,
                "question": original.question,
                "question_type": original.question_type,
                "expected_source_documents": original.model_dump()["source_documents"],
                "expected_document_ids": required,
                "expected_document_pages": [
                    {"source_document": s.source_document, "page_number": s.page_number}
                    for s in spans
                ],
                "retrieved_chunk_count": len(hits),
                "duplicate_chunk_hits": len(hits) - len({h.chunk_id for h in hits}),
                "strict": strict,
                "scorable_diagnostic": scorable_metrics(scorable, hits),
                "span_diagnostics": errors,
            }
        )
        diagnostics.extend(structure)
    return {
        "status": "completed_recorded_source_span_evaluation",
        "dataset_revision": revision,
        "family": family,
        "model": {
            "id": MODEL,
            "revision": MODEL_REVISION,
            "verification": "reuse_of_recorded_3ba_verification; not downloaded or loaded here",
        },
        "annotation_schema_version": context.header["annotation_schema_version"],
        "reviewer_independence": "Human alias declarations; not authenticated by software",
        "input_sha256": {
            "review": review_hash,
            "validation": validation_hash,
            "retrieval_results": retrieval_hash,
            "acquisition_manifest": context.header["acquisition_manifest_sha256"],
        },
        "dataset_artifacts": results["dataset_artifacts"],
        "filename_document_id_mapping": mapping,
        "indexed_documents": results["indexed_documents"],
        "configuration": {
            "ks": list(KS),
            "matching": "case-sensitive normalize_quote; one complete chunk",
            "ranking": "original recorded chunk positions, including duplicates",
            "aggregation": "question-macro; diagnostic excludes questions with no scorable spans",
            "recorded_retrieval": results["configuration"],
        },
        "annotation_exclusions": fresh["acquisition_exclusions"],
        "structural_summary": summarize_structure(diagnostics),
        "cases": output,
        "metrics": aggregate(output),
        "recorded_retrieval_environment": results.get("environment"),
        "recorded_retrieval_timestamp": results.get("recorded_at"),
        "interpretation": (
            "Source-span coverage, not answer correctness or general retrieval accuracy"
        ),
    }


def run(acquisition_dir, annotations, validation, retrieval_results, output_dir):
    output_dir = acquisition.confined(output_dir)
    if output_dir.exists():
        raise ValueError("Use a fresh ignored output directory; existing results are preserved")
    started = perf_counter()
    record = {"status": "blocked", "llm_called": False, "embeddings_called": False}
    try:
        record.update(evaluate_inputs(acquisition_dir, annotations, validation, retrieval_results))
    except (OSError, ValueError, KeyError, TypeError) as exc:
        record["failure"] = str(exc)
    record.update(
        {
            "recorded_at": timestamp(),
            "scoring_seconds": perf_counter() - started,
            "python": platform.python_version(),
            "platform": platform.platform(),
            "pypdf": importlib.metadata.version("pypdf"),
            "source_sha256": {
                name: hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest()
                for name in (
                    "external_source_span_retrieval.py",
                    "external_source_span_metrics.py",
                    "source_span_structure.py",
                )
            },
        }
    )
    write_new_json(output_dir / "results.json", record)
    return record


def main(argv=None):
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--acquisition-dir", type=Path, required=True)
    cli.add_argument("--annotations", type=Path, required=True)
    cli.add_argument("--validation-report", type=Path, required=True)
    cli.add_argument("--retrieval-results", type=Path, required=True)
    cli.add_argument(
        "--output-dir", type=Path, required=True, help="Fresh ignored output directory"
    )
    args = cli.parse_args(argv)
    try:
        record = run(
            args.acquisition_dir,
            args.annotations,
            args.validation_report,
            args.retrieval_results,
            args.output_dir,
        )
    except (OSError, ValueError) as exc:
        logging.error("Evaluation not started: %s", exc)
        return 1
    if record["status"] == "blocked":
        logging.error("Evaluation blocked; no scores generated: %s", record["failure"])
        return 1
    print(
        json.dumps(
            {"metrics": record["metrics"], "structural_summary": record["structural_summary"]},
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
