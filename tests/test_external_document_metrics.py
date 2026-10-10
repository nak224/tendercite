"""Deterministic candidate-label eligibility/metrics, original PDFs, no model/network calls."""

from types import SimpleNamespace

import pytest
from test_external_benchmark import FAMILY, REVISION, FakeHub, row

from evaluation import external_benchmark as acquisition
from evaluation.document_retrieval_metrics import (
    aggregate,
    case_metrics,
    evaluate_cases,
    rank_documents,
)
from evaluation.external_document_cases import prepare_cases
from evaluation.external_qa import QARecord
from tendercite.services.retrieval.base import SearchHit


@pytest.fixture
def corpus(tmp_path, monkeypatch):
    monkeypatch.setattr(acquisition, "EXTERNAL_ROOT", tmp_path)
    directory = tmp_path / "acquired"
    hub = FakeHub(
        [
            row(),
            row(
                id="generated-multi",
                question_type="Cross-document factual",
                type_number=3,
                hop_count=2,
                source_documents=["notice.pdf", "contract.pdf"],
                relevant_chunks=["notice.pdf_003", "contract.pdf_004"],
            ),
            row(
                id="generated-unanswerable",
                question_type="Unanswerable",
                type_number=5,
                hop_count=0,
                source_documents=[],
                relevant_chunks=[],
            ),
            row(
                id="other-family",
                family="Other_Family",
                source_documents=["unrequested.pdf"],
                documents_in_family=["unrequested.pdf"],
                relevant_chunks=["unrequested.pdf_000"],
            ),
        ]
    )
    acquisition.acquire(REVISION, directory, family=FAMILY, download_pdfs=True, source=hub)
    return directory


def prepare(directory):
    return prepare_cases(directory, family=FAMILY, revision=REVISION)


def hit(doc_id, name, chunk_id, score=0.9):
    return SearchHit(
        document_id=doc_id,
        document_name=name,
        chunk_id=chunk_id,
        page_number=1,
        text="Original synthetic text.",
        score=score,
    )


def test_eligibility_multiple_references_unanswerables_and_no_family_leak(corpus):
    prepared = prepare(corpus)
    assert [case.id for case in prepared.eligible] == ["generated-lookup", "generated-multi"]
    assert prepared.eligible[1].source_documents == ["notice.pdf", "contract.pdf"]
    assert prepared.eligible[1].question_type == "Cross-document factual"
    assert set(prepared.documents) == {"notice.pdf", "contract.pdf"}
    assert prepared.excluded[0]["id"] == "generated-unanswerable"
    assert "Unanswerable" in prepared.excluded[0]["reasons"][0]
    assert prepare(corpus).excluded == prepared.excluded


def test_missing_one_pdf_excludes_entire_multi_reference_case(corpus):
    (corpus / f"artifacts/data/{FAMILY}/contract.pdf").unlink()
    prepared = prepare(corpus)
    assert [case.id for case in prepared.eligible] == ["generated-lookup"]
    exclusion = next(case for case in prepared.excluded if case["id"] == "generated-multi")
    assert exclusion["source_documents"] == ["notice.pdf", "contract.pdf"]
    assert "integrity_failed" in exclusion["reasons"][0]


def test_tampered_bytes_cannot_be_rescued_by_stale_passed_report(corpus):
    (corpus / f"artifacts/data/{FAMILY}/notice.pdf").write_bytes(b"tampered")
    prepared = prepare(corpus)
    assert not prepared.eligible
    assert len(prepared.excluded) == 3
    assert "integrity_failed" in prepared.excluded[0]["reasons"][0]


@pytest.mark.parametrize("filename", ["manifest.json", "validation.json", acquisition.QA_FILENAME])
def test_missing_dataset_artifact_fails_explicitly(corpus, filename):
    path = corpus / filename
    if filename == acquisition.QA_FILENAME:
        path = corpus / "artifacts" / filename
    path.unlink()
    with pytest.raises(FileNotFoundError):
        prepare(corpus)


def test_mismatched_revision_is_rejected(corpus):
    with pytest.raises(ValueError, match="pinned revision/family"):
        prepare_cases(corpus, family=FAMILY, revision="b" * 40)


def test_invalid_labels_are_rejected_without_repair(corpus, monkeypatch):
    # Metadata validated against the inventory: missing reference is not silently removed.
    hub = FakeHub(
        [
            row(
                source_documents=["missing.pdf"],
                relevant_chunks=["missing.pdf_001"],
                documents_in_family=["missing.pdf"],
            )
        ]
    )
    acquisition.acquire(
        REVISION, corpus.parent / "invalid", family=FAMILY, download_pdfs=True, source=hub
    )
    with pytest.raises(ValueError, match="labels cannot"):
        prepare(corpus.parent / "invalid")


def test_ranking_deduplicates_chunks_with_first_appearance_and_mapping():
    hits = [
        hit("A", "a.pdf", "a1", 0.99),
        hit("A", "a.pdf", "a2", 0.98),
        hit("B", "b.pdf", "b1", 0.9),
    ]
    ranking = rank_documents(hits, {"a.pdf": "A", "b.pdf": "B"})
    assert [row["document_id"] for row in ranking] == ["A", "B"]
    assert ranking[1]["document_rank"] == 2
    assert ranking[1]["first_chunk_rank"] == 3
    assert ranking[0]["score"] == 0.99
    assert ranking[0]["first_chunk_id"] == "a1"


@pytest.mark.parametrize("wrong", [hit("OTHER", "a.pdf", "x"), hit("A", "wrong.pdf", "x")])
def test_unmapped_or_wrongly_named_document_is_rejected(wrong):
    with pytest.raises(ValueError, match="selected-family"):
        rank_documents([wrong], {"a.pdf": "A"})


def test_any_all_and_single_reference_mrr_use_unique_document_ranks():
    ranking = rank_documents(
        [hit("A", "a.pdf", "a"), hit("B", "b.pdf", "b")], {"a.pdf": "A", "b.pdf": "B"}
    )
    multi = case_metrics(["A", "B"], ranking, (1, 2, 3))
    assert multi["any_referenced_document_at_k"] == {"1": True, "2": True, "3": True}
    assert multi["all_referenced_documents_at_k"] == {"1": False, "2": True, "3": True}
    assert multi["single_reference_reciprocal_rank"] is None
    single = case_metrics(["B"], ranking, (1, 2, 3))
    assert single["single_reference_reciprocal_rank"] == 0.5
    assert single["expected_document_ranks"] == {"B": 2}
    rows = [
        {"question_type": "Single-document lookup", **single},
        {"question_type": "Cross-document factual", **multi},
    ]
    summary = aggregate(rows, (1, 2, 3))
    assert summary["combined"]["any_referenced_document_at_k"]["1"] == 0.5
    assert summary["combined"]["all_referenced_documents_at_k"]["1"] == 0
    assert summary["combined"]["single_reference_mrr"] == 0.5
    assert summary["combined"]["single_reference_cases"] == 1
    assert summary["by_question_type"]["Cross-document factual"]["single_reference_mrr"] is None


def test_empty_results_are_misses_and_empty_dataset_has_no_fabricated_scores():
    metrics = case_metrics(["A"], [], (1, 2, 3))
    assert not any(metrics["any_referenced_document_at_k"].values())
    assert not any(metrics["all_referenced_documents_at_k"].values())
    assert metrics["single_reference_reciprocal_rank"] == 0
    assert metrics["expected_document_ranks"] == {"A": None}
    assert aggregate([], (1, 2, 3))["combined"]["any_referenced_document_at_k"]["1"] is None


def test_invalid_metric_inputs_are_rejected():
    with pytest.raises(ValueError, match="at least one"):
        case_metrics([], [], (1,))
    with pytest.raises(ValueError, match="search limit"):
        case_metrics(["A"], [], (51,))
    with pytest.raises(ValueError, match="unique-document"):
        case_metrics(["A"], [{"document_id": "A"}, {"document_id": "A"}], (1,))


def test_original_question_and_full_family_filter_are_used_for_every_case():
    query = "  An original synthetic question?  "
    case = QARecord.model_validate(row(question=query))
    requests = []

    def search(request):
        requests.append(request)
        return [hit("B", "contract.pdf", "b1"), hit("A", "notice.pdf", "a1")]

    result = evaluate_cases(
        [case], {"notice.pdf": "A", "contract.pdf": "B"}, SimpleNamespace(search=search)
    )
    assert requests[0].query == query
    assert requests[0].document_ids == ["A", "B"]  # Never oracle-filter to expected A only.
    assert requests[0].top_k == 50
    measured = result["cases"][0]
    assert measured["expected_source_documents"] == ["notice.pdf"]
    assert measured["expected_document_ids"] == ["A"]
    assert measured["question_type"] == case.question_type
    assert measured["single_reference_reciprocal_rank"] == 0.5
    assert "answer" not in measured


def test_encrypted_source_excludes_single_and_entire_multi_document_case(corpus):
    from io import BytesIO

    from pypdf import PdfReader, PdfWriter

    hub = FakeHub(
        [
            row(),
            row(
                id="two-source",
                source_documents=["notice.pdf", "contract.pdf"],
                relevant_chunks=["notice.pdf_0", "contract.pdf_0"],
            ),
        ]
    )
    key = f"data/{FAMILY}/notice.pdf"
    writer = PdfWriter(clone_from=PdfReader(BytesIO(hub.data[key])))
    writer.encrypt("original-fixture-password")
    output = BytesIO()
    writer.write(output)
    hub.data[key] = output.getvalue()
    directory = corpus.parent / "encrypted"
    acquisition.acquire(REVISION, directory, family=FAMILY, download_pdfs=True, source=hub)
    prepared = prepare(directory)
    assert not prepared.eligible
    assert [case["id"] for case in prepared.excluded] == ["generated-lookup", "two-source"]
    assert all("Encrypted PDFs" in case["reasons"][0] for case in prepared.excluded)
    assert set(prepared.documents) == {"contract.pdf"}


def test_partial_multi_document_coverage_does_not_count_as_all():
    metrics = case_metrics(["A", "B"], [{"document_id": "A"}], (1, 2, 3))
    assert all(metrics["any_referenced_document_at_k"].values())
    assert not any(metrics["all_referenced_documents_at_k"].values())
    assert metrics["expected_document_ranks"] == {"A": 1, "B": None}
    assert metrics["single_reference_reciprocal_rank"] is None


def test_expected_reference_without_mapping_is_not_silently_dropped():
    with pytest.raises(ValueError, match="no indexed"):
        evaluate_cases([QARecord.model_validate(row())], {"contract.pdf": "B"}, None)
