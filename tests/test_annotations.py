"""Only original generated procurement text/PDFs; no network, models or real documents."""

import hashlib
import json
from pathlib import Path

import pytest

from evaluation.annotations import (
    ANNOTATION_SCHEMA_VERSION,
    SPLIT_SEED,
    AnnotationFile,
    parse_snapshot,
    tender_split,
    validate_annotations,
)
from evaluation.generate import make_pdf
from evaluation.local_corpus import MetadataFile, register_directory
from evaluation.validate_annotations import main

DEADLINE = "Die Angebotsfrist endet am 30. November 2026 um 12:00 Uhr."
REFERENCE = "Es sind zwei vergleichbare Referenzprojekte nachzuweisen."
SECURITY = "Privilegierte Zugriffe müssen mit zwei Faktoren abgesichert sein."
BACKGROUND = (
    "Die Vergabe betrifft die Bereitstellung und Wartung einer Softwarelösung. "
    "Die Unterlagen erläutern das Verfahren und die Zuständigkeiten. "
    "Frühere Veranstaltungen und unverbindliche Muster dienen nur zur Orientierung."
)


def stored(corpus):
    return json.loads((corpus / "manifest.json").read_text(encoding="utf-8"))


@pytest.fixture
def package(tmp_path):
    inputs = tmp_path / "input"
    inputs.mkdir()
    (inputs / "notice.pdf").write_bytes(
        make_pdf([DEADLINE + "\n" + BACKGROUND, REFERENCE + "\n" + BACKGROUND])
    )
    (inputs / "specification.pdf").write_bytes(make_pdf([SECURITY + "\n" + BACKGROUND]))
    corpus = tmp_path / "corpus"
    register_directory(inputs, corpus, metadata=MetadataFile(defaults={"tender_id": "tender-a"}))
    docs = {doc["sources"][0]["original_filename"]: doc for doc in stored(corpus)["documents"]}
    return corpus, docs


def annotation(document, **changes):
    return {
        "annotation_id": "deadline-01",
        "tender_id": "tender-a",
        "document_id": document["document_id"],
        "sha256": document["sha256"],
        "page_number": 1,
        "source_quote": DEADLINE,
        "requirement_statement": "Das Angebot ist bis 30.11.2026, 12:00 Uhr einzureichen.",
        "category": "MUST",
        "requirement_type": "DEADLINE",
        "review_status": "VERIFIED",
        "author_alias": "annotator_01",
        "reviewer_alias": "reviewer_02",
        "review_notes": "Original synthetic fixture; source and interpretation reviewed.",
        "annotated_at": "2026-10-09T17:30:00+02:00",
        **changes,
    }


def payload(*annotations, **changes):
    return {
        "annotation_schema_version": ANNOTATION_SCHEMA_VERSION,
        "corpus_version": "real-pdf-1",
        "label_origin": "curated_human",
        "annotations": list(annotations),
        **changes,
    }


def failures(outcome, index=0):
    return " ".join(outcome.report["results"][index]["exclusion_reasons"])


def test_verified_quote_export_preserves_identity_review_and_versions(package):
    corpus, docs = package
    label = annotation(docs["notice.pdf"])
    result = validate_annotations(payload(label), corpus)
    assert result.report["validation_summary"]["evaluation_ready"] == 1
    assert not result.gold["exclusions"] and result.report["results"][0]["evaluation_ready"]
    exported = result.gold["annotations"][0]
    for key, value in label.items():
        assert exported[key] == value
    assert result.gold["document_hashes"] == {label["document_id"]: label["sha256"]}
    assert result.gold["corpus_version"] == "real-pdf-1"
    assert result.gold["annotation_schema_version"] == ANNOTATION_SCHEMA_VERSION
    assert result.gold["label_origin"] == "curated_human"
    assert result.gold["split_config"]["seed"] == SPLIT_SEED
    assert exported["split"] == result.gold["tender_splits"]["tender-a"]


@pytest.mark.parametrize(
    "changes,reason",
    [
        ({"source_quote": "An invented requirement."}, "Exact source quote"),
        ({"source_quote": DEADLINE.lower()}, "Exact source quote"),
        ({"source_quote": DEADLINE.replace(" ", "  ")}, "Exact source quote"),
        ({"source_quote": ""}, "source_quote"),
        ({"source_quote": " \n\t"}, "must not be blank"),
        ({"page_number": 2}, "Exact source quote"),
        ({"page_number": 3}, "outside the extracted PDF page range"),
        ({"page_number": 0}, "page_number"),
        ({"page_number": True}, "page_number"),
        ({"page_number": "1"}, "page_number"),
        ({"document_id": "sha256:" + "0" * 64}, "Document does not exist"),
        ({"sha256": "0" * 64}, "SHA-256 does not match"),
        ({"sha256": "not-a-hash"}, "sha256"),
        ({"tender_id": "another-tender"}, "association does not exist"),
        ({"category": "MANDATORY"}, "category"),
        ({"requirement_type": "UNKNOWN"}, "requirement_type"),
        ({"requirement_statement": " "}, "must not be blank"),
        ({"review_status": "CONFIRMED"}, "review_status"),
        ({"author_alias": "person@example.invalid"}, "author_alias"),
        ({"reviewer_alias": "Actual Person"}, "reviewer_alias"),
        ({"annotated_at": "2026-10-09T12:00:00"}, "timezone"),
        ({"annotated_at": 1791547200}, "ISO-8601"),
        ({"annotated_at": "1791547200"}, "ISO-8601"),
        ({"automatically_generated": True}, "Extra inputs"),
    ],
)
def test_invalid_annotation_reasons_are_preserved_and_excluded(package, changes, reason):
    corpus, docs = package
    result = validate_annotations(payload(annotation(docs["notice.pdf"], **changes)), corpus)
    assert result.gold["annotations"] == []
    assert result.report["validation_summary"]["invalid"] == 1
    assert reason in failures(result)
    assert result.gold["exclusions"][0]["exclusion_reasons"]


def test_existing_wrong_document_cannot_supply_another_documents_quote(package):
    corpus, docs = package
    result = validate_annotations(payload(annotation(docs["specification.pdf"])), corpus)
    assert "Exact source quote" in failures(result) and not result.gold["annotations"]


@pytest.mark.parametrize("reviewer", [None, "annotator_01"])
def test_verified_requires_independent_reviewer(package, reviewer):
    corpus, docs = package
    result = validate_annotations(
        payload(annotation(docs["notice.pdf"], reviewer_alias=reviewer)), corpus
    )
    assert "reviewer distinct from the author" in failures(result)
    assert result.gold["annotations"] == []


@pytest.mark.parametrize("duplicate_id", [True, False])
def test_all_duplicate_ids_or_source_labels_are_excluded(package, duplicate_id):
    corpus, docs = package
    first = annotation(docs["notice.pdf"])
    second = annotation(
        docs["notice.pdf"],
        annotation_id="deadline-01" if duplicate_id else "deadline-02",
        page_number=2 if duplicate_id else 1,
        source_quote=REFERENCE if duplicate_id else DEADLINE,
        requirement_type="REFERENCE" if duplicate_id else "DEADLINE",
        requirement_statement="An alternative wording of the same deadline.",
    )
    result = validate_annotations(payload(first, second), corpus)
    assert not result.gold["annotations"]
    assert result.report["validation_summary"]["duplicate_annotations"] == 2
    assert all("Duplicate annotation" in failures(result, index) for index in range(2))


def test_duplicate_id_with_schema_invalid_entry_still_excludes_valid_entry(package):
    corpus, docs = package
    first = annotation(docs["notice.pdf"])
    second = {"annotation_id": first["annotation_id"]}
    result = validate_annotations(payload(first, second), corpus)
    assert not result.gold["annotations"] and "Duplicate annotation ID" in failures(result)


def test_duplicate_source_with_invalid_reviewer_cannot_mask_ambiguity(package):
    corpus, docs = package
    first = annotation(docs["notice.pdf"])
    second = annotation(
        docs["notice.pdf"], annotation_id="different-id", reviewer_alias="annotator_01"
    )
    result = validate_annotations(payload(first, second), corpus)
    assert not result.gold["annotations"]
    assert all("Duplicate annotation source" in failures(result, index) for index in range(2))


def test_multi_pdf_tender_and_multiple_annotations_per_document(package):
    corpus, docs = package
    labels = [
        annotation(docs["notice.pdf"]),
        annotation(
            docs["notice.pdf"],
            annotation_id="references-01",
            page_number=2,
            source_quote=REFERENCE,
            requirement_type="REFERENCE",
            requirement_statement="Zwei vergleichbare Referenzprojekte sind nachzuweisen.",
        ),
        annotation(
            docs["specification.pdf"],
            annotation_id="security-01",
            source_quote=SECURITY,
            requirement_type="SECURITY",
            requirement_statement="Privilegierte Zugriffe benötigen Zwei-Faktor-Authentisierung.",
        ),
    ]
    result = validate_annotations(payload(*labels), corpus)
    assert len(result.gold["annotations"]) == 3 and len(result.gold["document_hashes"]) == 2
    assert len({entry["split"] for entry in result.gold["annotations"]}) == 1
    assert not result.gold["exclusions"]


def test_empty_and_partially_verified_gold_with_explicit_review_exclusions(package):
    corpus, docs = package
    empty = validate_annotations(payload(), corpus)
    assert empty.gold["annotations"] == [] and empty.gold["tender_splits"] == {}
    assert empty.report["validation_summary"]["total"] == 0
    labels = [
        annotation(docs["notice.pdf"]),
        annotation(
            docs["notice.pdf"],
            annotation_id="references-draft",
            page_number=2,
            source_quote=REFERENCE,
            requirement_type="REFERENCE",
            review_status="DRAFT",
            reviewer_alias=None,
        ),
        annotation(
            docs["specification.pdf"],
            annotation_id="security-rejected",
            source_quote=SECURITY,
            requirement_type="SECURITY",
            review_status="REJECTED",
            review_notes="Interpretation rejected by human reviewer.",
        ),
    ]
    result = validate_annotations(payload(*labels), corpus)
    assert result.report["validation_summary"]["valid"] == 3
    assert result.report["validation_summary"]["evaluation_ready"] == 1
    assert len(result.gold["exclusions"]) == 2
    assert "DRAFT" in failures(result, 1) and "REJECTED" in failures(result, 2)
    assert result.gold["exclusions"][1]["review_notes"] == labels[2]["review_notes"]


def test_split_is_deterministic_by_tender_independent_of_order_and_growth(package):
    corpus, docs = package
    labels = []
    # Associate the generated fixture with distinct synthetic tender IDs for split testing.
    manifest = stored(corpus)
    document = next(doc for doc in manifest["documents"] if doc["page_count"] == 2)
    for index in range(30):
        tender_id = f"synthetic-tender-{index:02}"
        manifest["tenders"][tender_id] = [document["document_id"]]
        document["sources"].append({"tender_id": tender_id})
        labels.append(annotation(document, tender_id=tender_id, annotation_id=f"label-{index:02}"))
    (corpus / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    first = validate_annotations(payload(*labels[:20]), corpus).gold
    reordered = validate_annotations(payload(*reversed(labels[:20])), corpus).gold
    extended = validate_annotations(payload(*labels), corpus).gold
    assert first["tender_splits"] == reordered["tender_splits"]
    assert all(
        extended["tender_splits"][key] == value for key, value in first["tender_splits"].items()
    )
    assert set(extended["tender_splits"].values()) == {
        "train",
        "development",
        "held_out_evaluation",
    }
    assert tender_split("tender-a") == tender_split("tender-a", SPLIT_SEED)
    assert tender_split("synthetic-tender-03", 224) == "development"
    assert any(
        tender_split(label["tender_id"], 42) != label["split"] for label in extended["annotations"]
    )


def test_multiline_quote_requires_the_actual_extracted_line_breaks(package):
    corpus, docs = package
    document = docs["notice.pdf"]
    page = parse_snapshot(corpus, document)[0].text
    quote = "\n".join(page.splitlines()[:2])
    result = validate_annotations(payload(annotation(document, source_quote=quote)), corpus)
    assert "\n" in quote and len(result.gold["annotations"]) == 1
    altered = validate_annotations(
        payload(annotation(document, source_quote=quote.replace("\n", " "))), corpus
    )
    assert "Exact source quote" in failures(altered)


def test_changed_snapshot_excludes_every_reference_but_keeps_unaffected_document(
    package, monkeypatch
):
    from evaluation import annotations

    corpus, docs = package
    notice = docs["notice.pdf"]
    original_read = annotations.read_pdf_bytes

    def changed_read(path):
        return (
            b"changed after integrity verification"
            if path.name == notice["sha256"] + ".pdf"
            else original_read(path)
        )

    monkeypatch.setattr(annotations, "read_pdf_bytes", changed_read)
    labels = [
        annotation(notice),
        annotation(notice, annotation_id="reference-01", page_number=2, source_quote=REFERENCE),
        annotation(docs["specification.pdf"], annotation_id="security-01", source_quote=SECURITY),
    ]
    result = validate_annotations(payload(*labels), corpus)
    assert [label["annotation_id"] for label in result.gold["annotations"]] == ["security-01"]
    assert all("SHA-256/size mismatch" in failures(result, index) for index in range(2))


def test_cli_invalid_header_writes_no_export(package, tmp_path, caplog):
    corpus, _ = package
    source, export = tmp_path / "invalid-header.json", tmp_path / "gold.json"
    source.write_text(json.dumps(payload(label_origin="automatic_candidates")), encoding="utf-8")
    assert main([str(source), "--corpus-dir", str(corpus), "--export", str(export)]) == 1
    assert "Invalid annotation header" in caplog.text and not export.exists()


@pytest.mark.parametrize("tamper", ["bytes", "missing", "path", "page_count", "association"])
def test_corpus_tampering_is_reported_and_prevents_gold(package, tamper):
    corpus, docs = package
    document = docs["notice.pdf"]
    manifest = stored(corpus)
    record = next(
        doc for doc in manifest["documents"] if doc["document_id"] == document["document_id"]
    )
    if tamper == "bytes":
        (corpus / record["path"]).write_bytes(b"altered bytes")
    elif tamper == "missing":
        (corpus / record["path"]).unlink()
    elif tamper == "path":
        record["path"] = "../outside.pdf"
    elif tamper == "page_count":
        record["page_count"] = 99
    else:
        record["sources"] = [{"tender_id": "wrong-tender"}]
    (corpus / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    result = validate_annotations(payload(annotation(document)), corpus)
    assert not result.gold["annotations"] and failures(result)
    if tamper != "association":
        assert result.report["corpus_errors"]


def test_non_object_records_and_header_versions_or_candidates_fail_explicitly(package):
    corpus, _ = package
    result = validate_annotations(payload(None, 3, {}, ["invalid"]), corpus)
    assert result.report["validation_summary"]["invalid"] == 4
    assert all(record["exclusion_reasons"] for record in result.gold["exclusions"])
    for changes in [
        {"annotation_schema_version": "unknown"},
        {"label_origin": "automatically_generated_candidates"},
    ]:
        with pytest.raises(ValueError, match="Invalid annotation header"):
            validate_annotations(payload(**changes), corpus)
    mismatched = validate_annotations(payload(corpus_version="unknown"), corpus)
    assert "version mismatch" in " ".join(mismatched.report["corpus_errors"])


def test_missing_corpus_reports_failure_for_each_annotation(tmp_path):
    document = {"document_id": "sha256:" + "0" * 64, "sha256": "0" * 64}
    labels = [annotation(document), annotation(document, annotation_id="another")]
    result = validate_annotations(payload(*labels), tmp_path / "missing")
    assert len(result.gold["exclusions"]) == 2
    assert all("No corpus manifest" in failures(result, index) for index in range(2))


def test_cli_partial_export_is_nonzero_reproducible_and_leaves_corpus_unchanged(package, tmp_path):
    corpus, docs = package
    source = tmp_path / "annotations.json"
    source.write_text(
        json.dumps(payload(annotation(docs["notice.pdf"]), {"annotation_id": "invalid"})),
        encoding="utf-8",
    )
    before = {path: path.read_bytes() for path in corpus.rglob("*") if path.is_file()}
    report, export = tmp_path / "report.json", tmp_path / "gold.json"
    command = [
        str(source),
        "--corpus-dir",
        str(corpus),
        "--report",
        str(report),
        "--export",
        str(export),
    ]
    assert main(command) == 1
    gold = json.loads(export.read_text(encoding="utf-8"))
    assert len(gold["annotations"]) == 1 and len(gold["exclusions"]) == 1
    assert gold["annotations_sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()
    first_report, first_gold = report.read_bytes(), export.read_bytes()
    assert main(command) == 1
    assert report.read_bytes() == first_report and export.read_bytes() == first_gold
    assert all(path.read_bytes() == content for path, content in before.items())


@pytest.mark.parametrize("state", ["empty", "draft", "verified"])
def test_cli_success_with_empty_or_unverified_labels_does_not_invent_gold(package, tmp_path, state):
    corpus, docs = package
    labels = (
        [] if state == "empty" else [annotation(docs["notice.pdf"], review_status=state.upper())]
    )
    source, export = tmp_path / "labels.json", tmp_path / "gold.json"
    source.write_text(json.dumps(payload(*labels)), encoding="utf-8")
    assert main([str(source), "--corpus-dir", str(corpus), "--export", str(export)]) == 0
    assert len(json.loads(export.read_text())["annotations"]) == int(state == "verified")
    assert (corpus / "annotation-validation.json").is_file()


def test_cli_does_not_overwrite_source_manifest_or_pdfs(package, tmp_path):
    corpus, docs = package
    source = tmp_path / "labels.json"
    source.write_text(json.dumps(payload(annotation(docs["notice.pdf"]))), encoding="utf-8")
    for target in [source, corpus / "manifest.json", corpus / docs["notice.pdf"]["path"]]:
        before = target.read_bytes()
        assert main([str(source), "--corpus-dir", str(corpus), "--report", str(target)]) == 1
        assert target.read_bytes() == before
    output = tmp_path / "same.json"
    assert main([str(source), "--report", str(output), "--export", str(output)]) == 1


def test_committed_json_schema_matches_typed_format():
    root = Path(__file__).resolve().parents[1]
    schema = json.loads((root / "evaluation/annotation-schema.json").read_text(encoding="utf-8"))
    assert schema == AnnotationFile.model_json_schema()
