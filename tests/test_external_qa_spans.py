"""Original synthetic PDFs and QA only; reviewer declarations here are test fixtures."""

import copy
import hashlib
import json
import subprocess
from pathlib import Path

import pytest
from test_external_benchmark import FAMILY, REVISION, TEXT
from test_external_document_metrics import corpus, prepare  # noqa: F401

from evaluation import external_benchmark as acquisition
from evaluation import prepare_external_qa_spans as prepare_cli
from evaluation import validate_external_qa_spans as validate_cli
from evaluation.external_qa_spans import (
    ReviewFile,
    export_pages,
    load_review_context,
    make_review_template,
    parsed_reference_pages,
    write_new_json,
)


@pytest.fixture
def context(corpus):  # noqa: F811 - pytest injects the shared imported PDF corpus fixture.
    prepared = prepare(corpus)
    results = corpus.parent / "synthetic-results.json"
    write_new_json(
        results,
        {
            "status": "completed_real_document_retrieval",
            "family": FAMILY,
            "dataset_revision": REVISION,
            "eligible_cases": len(prepared.eligible),
            "exclusions": prepared.excluded,
            "dataset_artifacts": prepared.manifest["files"],
            "cases": [
                {
                    "id": case.id,
                    "question": case.question,
                    "question_type": case.question_type,
                    "expected_source_documents": case.source_documents,
                }
                for case in prepared.eligible
            ],
        },
    )
    return load_review_context(corpus, results, family=FAMILY, revision=REVISION)


def evidence(context, name="notice.pdf", **changes):
    # These aliases and VERIFIED declarations are wholly synthetic, never pilot annotations.
    return {
        "source_document": name,
        "pdf_sha256": context.documents[name]["sha256"],
        "page_number": 1,
        "exact_source_quote": "The supplier must provide two references",
        "interpretation_note": None,
        "review_status": "VERIFIED",
        "author_alias": "synthetic-author",
        "reviewer_alias": "synthetic-reviewer",
        "review_notes": "Synthetic independent-review test fixture.",
        **changes,
    }


def filled(context):
    payload = make_review_template(context)
    for question in payload["questions"]:
        question["evidence"] = [
            evidence(context, ref["filename"]) for ref in question["source_documents"]
        ]
    return payload


def test_template_preserves_original_metadata_and_has_no_trusted_answers(context):
    payload = make_review_template(context)
    assert payload == make_review_template(context)
    assert [q["question_id"] for q in payload["questions"]] == [
        "generated-lookup",
        "generated-multi",
    ]
    assert payload["dataset_revision"] == REVISION
    for question in payload["questions"]:
        expected = context.questions[question["question_id"]]
        assert {k: v for k, v in question.items() if k != "evidence"} == expected.model_dump()
        assert question["evidence"] == []
        assert "answer" not in question and "relevant_chunks" not in question
    assert "Two comparable references." not in json.dumps(payload)
    assert context.exclusions[0]["id"] == "generated-unanswerable"
    schema = json.loads(
        (Path(__file__).parents[1] / "evaluation/external-qa-source-span-schema.json").read_text()
    )
    assert schema == ReviewFile.model_json_schema()
    assert "requirement_category" not in json.dumps(schema)


@pytest.mark.parametrize("mutation", ["question", "references", "hash", "revision", "duplicate"])
def test_changed_3ba_metadata_is_not_silently_repaired(context, mutation):
    path = context.acquisition_dir.parent / "synthetic-results.json"
    results = json.loads(path.read_text())
    if mutation == "question":
        results["cases"][0]["question"] = "Changed original question"
    elif mutation == "references":
        results["cases"][0]["expected_source_documents"] = ["contract.pdf"]
    elif mutation == "hash":
        for artifact in results["dataset_artifacts"]:
            artifact["sha256"] = "b" * 64
    elif mutation == "revision":
        results["dataset_revision"] = "b" * 40
    else:
        results["cases"][1] = results["cases"][0]
    path.write_text(json.dumps(results))
    with pytest.raises(ValueError):
        load_review_context(context.acquisition_dir, path, family=FAMILY, revision=REVISION)


def test_export_is_exact_parser_output_numbered_hashed_and_family_confined(context):
    target = context.acquisition_dir.parent / "pages"
    expected = parsed_reference_pages(context)
    index = export_pages(context, target)
    assert {d["filename"] for d in index["documents"]} == {"notice.pdf", "contract.pdf"}
    assert sum(len(d["pages"]) for d in index["documents"]) == 3
    for document in index["documents"]:
        for page in document["pages"]:
            raw = (target / page["text_file"]).read_bytes()
            assert raw == expected[document["filename"]][page["page_number"]].encode()
            assert hashlib.sha256(raw).hexdigest() == page["text_sha256"]
    with pytest.raises(FileExistsError):
        export_pages(context, target)


def test_empty_review_is_incomplete_without_scores(context):
    report = validate_cli.validate_review(make_review_template(context), context)
    assert report["status"] == "INCOMPLETE"
    assert report["evidence_complete_questions"] == 0
    assert report["retrieval_scoring_performed"] is False
    assert all(not q["verified_source_spans"] for q in report["questions"])


def test_multiple_spans_and_multi_document_completeness(context):
    payload = filled(context)
    payload["questions"][0]["evidence"].append(
        evidence(context, exact_source_quote="administrative details")
    )
    report = validate_cli.validate_review(payload, context)
    assert report["status"] == "COMPLETE"
    assert report["evidence_complete_questions"] == 2
    assert report["questions"][0]["required_document_review"][0]["verified_span_count"] == 2
    payload["questions"][1]["evidence"].pop()
    report = validate_cli.validate_review(payload, context)
    assert report["status"] == "INCOMPLETE"
    assert report["evidence_complete_questions"] == 1
    assert report["questions"][1]["required_document_review"][1]["verified_span_count"] == 0


@pytest.mark.parametrize(
    "changes,reason",
    [
        ({"page_number": 2}, "Page number does not exist"),
        ({"exact_source_quote": "Two references are required."}, "does not occur verbatim"),
        ({"exact_source_quote": "the supplier must provide two references"}, "verbatim"),
        ({"source_document": "contract.pdf"}, "not an original reference"),
        ({"source_document": "unrequested.pdf"}, "not an original reference"),
        ({"pdf_sha256": "b" * 64}, "hash does not match"),
        ({"pdf_sha256": "invalid"}, "pattern"),
        ({"page_number": 0}, "greater than or equal"),
        ({"page_number": True}, "integer"),
        ({"exact_source_quote": " "}, "must not be blank"),
        ({"review_status": "APPROVED"}, "Input should be"),
        ({"reviewer_alias": None}, "reviewer distinct"),
        ({"reviewer_alias": "synthetic-author"}, "reviewer distinct"),
        ({"author_alias": "person@example.com"}, "pattern"),
        ({"review_notes": ""}, "nonblank review notes"),
        ({"review_status": "REJECTED", "review_notes": " "}, "nonblank review notes"),
    ],
)
def test_invalid_evidence_has_explicit_reasons_and_is_excluded(context, changes, reason):
    payload = filled(context)
    payload["questions"][0]["evidence"][0].update(changes)
    report = validate_cli.validate_review(payload, context)
    question = report["questions"][0]
    assert report["status"] == "INVALID"
    assert not question["evidence_complete"] and not question["verified_source_spans"]
    assert reason in " ".join(question["evidence_results"][0]["validation_errors"])


def test_quote_on_different_existing_page_does_not_validate(context):
    payload = filled(context)
    payload["questions"][1]["evidence"][1].update(
        exact_source_quote="A contract annex.", page_number=2
    )
    report = validate_cli.validate_review(payload, context)
    assert "verbatim" in report["questions"][1]["evidence_results"][1]["validation_errors"][0]


@pytest.mark.parametrize("status", ["DRAFT", "REJECTED"])
def test_unverified_statuses_preserve_notes_but_exclude_spans(context, status):
    payload = filled(context)
    payload["questions"][0]["annotation_notes"] = "Synthetic ambiguity note."
    payload["questions"][0]["evidence"][0].update(review_status=status, reviewer_alias=None)
    report = validate_cli.validate_review(payload, context)
    question = report["questions"][0]
    assert report["status"] == "INCOMPLETE"
    assert not question["verified_source_spans"]
    assert question["annotation_notes"] == "Synthetic ambiguity note."
    result = question["evidence_results"][0]
    assert not result["validation_errors"] and not result["independently_verified"]
    assert status in result["exclusion_reasons"][0]
    assert result["review_notes"]


def test_duplicate_evidence_all_copies_excluded_but_cross_question_reuse_allowed(context):
    payload = filled(context)
    assert validate_cli.validate_review(payload, context)["status"] == "COMPLETE"
    duplicate = copy.deepcopy(payload["questions"][0]["evidence"][0])
    duplicate.update(review_status="DRAFT", interpretation_note="Different note, same source.")
    payload["questions"][0]["evidence"].append(duplicate)
    report = validate_cli.validate_review(payload, context)
    question = report["questions"][0]
    assert report["status"] == "INVALID" and not question["verified_source_spans"]
    assert all(
        "Duplicate evidence" in r["validation_errors"][0] for r in question["evidence_results"]
    )


@pytest.mark.parametrize("change", ["missing", "duplicate", "question", "type", "refs", "header"])
def test_original_cases_cannot_be_dropped_changed_or_reassigned(context, change):
    payload = filled(context)
    if change == "missing":
        payload["questions"].pop()
    elif change == "duplicate":
        payload["questions"].append(copy.deepcopy(payload["questions"][0]))
    elif change == "question":
        payload["questions"][0]["question"] = "Edited question"
    elif change == "type":
        payload["questions"][0]["question_type"] = "Implicit multi-hop"
    elif change == "refs":
        payload["questions"][1]["source_documents"].pop()
    else:
        payload["eligibility_results_sha256"] = "b" * 64
    report = validate_cli.validate_review(payload, context)
    assert report["status"] == "INVALID"
    assert report["global_errors"] or any(q["validation_errors"] for q in report["questions"])


def test_pdf_tampering_rejected_even_with_unchanged_export_and_manifest(context):
    export_pages(context, context.acquisition_dir.parent / "pages")
    context.documents["notice.pdf"]["local_path"].write_bytes(b"tampered")
    with pytest.raises(ValueError):
        validate_cli.validate_review(filled(context), context)


def test_edited_page_exports_cannot_validate_an_invented_quote(context):
    target = context.acquisition_dir.parent / "pages"
    index = export_pages(context, target)
    exported = target / index["documents"][0]["pages"][0]["text_file"]
    exported.write_text("A fabricated supporting passage.")
    payload = filled(context)
    payload["questions"][0]["evidence"][0]["exact_source_quote"] = (
        "A fabricated supporting passage."
    )
    report = validate_cli.validate_review(payload, context)
    assert report["status"] == "INVALID"
    assert not report["questions"][0]["verified_source_spans"]


def test_complete_cli_report_contains_only_synthetic_reviewed_spans(context, monkeypatch):
    monkeypatch.setattr(validate_cli, "load_review_context", lambda *a: context)
    root = context.acquisition_dir.parent
    template, report = root / "synthetic-reviewed.json", root / "complete.json"
    write_new_json(template, filled(context))
    assert (
        validate_cli.main(
            [
                str(template),
                "--acquisition-dir",
                str(context.acquisition_dir),
                "--retrieval-results",
                str(root / "synthetic-results.json"),
                "--report",
                str(report),
            ]
        )
        == 0
    )
    result = json.loads(report.read_text())
    assert result["status"] == "COMPLETE"
    assert result["annotations_sha256"] == hashlib.sha256(template.read_bytes()).hexdigest()
    assert result["evidence_complete_questions"] == 2
    assert sum(len(q["verified_source_spans"]) for q in result["questions"]) == 3


def test_cli_is_offline_preserves_review_files_and_reports_incomplete(context, monkeypatch):
    def fail_network(*args, **kwargs):
        pytest.fail("Network acquisition must not run in source-span preparation")

    monkeypatch.setattr(acquisition, "HuggingFaceSource", fail_network)
    for module in [prepare_cli, validate_cli]:
        monkeypatch.setattr(module, "load_review_context", lambda *a: context)
    root = context.acquisition_dir.parent
    template, pages, report = root / "review.json", root / "pages", root / "report.json"
    common = [
        "--acquisition-dir",
        str(context.acquisition_dir),
        "--retrieval-results",
        str(root / "synthetic-results.json"),
    ]
    assert prepare_cli.main(common + ["--template", str(template), "--pages-dir", str(pages)]) == 0
    original = template.read_bytes()
    assert validate_cli.main([str(template), *common, "--report", str(report)]) == 1
    assert json.loads(report.read_text())["status"] == "INCOMPLETE"
    assert prepare_cli.main(common + ["--template", str(template)]) == 1
    assert validate_cli.main([str(template), *common, "--report", str(template)]) == 1
    assert template.read_bytes() == original


def test_local_only_outputs_and_git_ignore_prevent_source_content_leak(context, tmp_path):
    outside = tmp_path.parent / "must-not-create-source-review.json"
    with pytest.raises(ValueError, match="data/evaluation/external"):
        write_new_json(outside, make_review_template(context))
    assert not outside.exists()
    link = tmp_path / "link"
    link.symlink_to(tmp_path.parent, target_is_directory=True)
    with pytest.raises(ValueError):
        write_new_json(link / "source.json", make_review_template(context))
    paths = [
        "data/evaluation/external/synthetic/review.json",
        "data/evaluation/external/synthetic/pages/page-0001.txt",
        "data/evaluation/external/synthetic/validation.json",
        "data/evaluation/external/synthetic/notice.pdf",
    ]
    result = subprocess.run(
        ["git", "check-ignore", "--no-index", *paths],
        cwd=Path(__file__).parents[1],
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout.splitlines() == paths
    assert TEXT not in json.dumps(ReviewFile.model_json_schema())
