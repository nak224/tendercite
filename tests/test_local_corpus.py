"""Original generated fixtures only; no real third-party PDFs or external requests."""

import hashlib
import json
import subprocess
from io import BytesIO
from pathlib import Path

import pytest
from pypdf import PdfReader, PdfWriter

from evaluation import local_corpus
from evaluation.generate import make_pdf
from evaluation.import_pdfs import main
from evaluation.local_corpus import (
    MetadataFile,
    load_corpus,
    register_directory,
    verify_corpus,
)

TEXT = (
    "Für den Betrieb der Software sind zwei Referenzen vorzulegen. "
    "Die Angebotsfrist endet am angegebenen Datum. Der Auftrag umfasst technische "
    "Unterstützung, Wartung und die Bereitstellung geeigneter Fachkräfte."
)


@pytest.fixture
def folders(tmp_path):
    inputs = tmp_path / "input"
    inputs.mkdir()
    return inputs, tmp_path / "corpus"


def metadata(**overrides):
    return {
        "tender_id": "generated-tender-a",
        "source_urls": ["https://example.invalid/procurement/notice.pdf"],
        "retrieval_date": "2026-10-09",
        "provenance_notes": "Self-generated test fixture; not downloaded procurement text.",
        "rights_notes": "Original fixture created for this automated test.",
        "provenance_status": "verified",
        "rights_status": "verified",
        "declared_language": "de",
        **overrides,
    }


def manifest(corpus):
    return json.loads((corpus / "manifest.json").read_text(encoding="utf-8"))


def test_single_pdf_exact_bytes_id_pages_and_provenance(folders):
    inputs, corpus = folders
    content = make_pdf([TEXT, TEXT + " Zusätzliche Vertragsbedingungen."])
    (inputs / "Bekanntmachung.PDF").write_bytes(content)
    source = metadata(ted_publication_id="00123456-2026")
    run = register_directory(inputs, corpus, metadata=MetadataFile(defaults=source))
    assert run["status"] == "completed"
    record = manifest(corpus)["documents"][0]
    digest = hashlib.sha256(content).hexdigest()
    assert record["sha256"] == digest and record["document_id"] == f"sha256:{digest}"
    assert record["page_count"] == 2 and record["validation"]["status"] == "passed"
    assert record["bytes"] == len(content) and (corpus / record["path"]).read_bytes() == content
    assert (inputs / "Bekanntmachung.PDF").read_bytes() == content
    provenance = record["sources"][0]
    assert provenance["original_filename"] == "Bekanntmachung.PDF"
    for key in ("source_urls", "retrieval_date", "provenance_notes", "rights_notes"):
        assert provenance[key] == source[key]
    assert provenance["ted_publication_id"] == "123456-2026" and provenance["flags"] == []
    assert verify_corpus(corpus, manifest(corpus)) == []
    assert not list(corpus.glob("tmp*.pdf"))


def test_multiple_pdfs_one_tender_and_per_file_overrides(folders):
    inputs, corpus = folders
    (inputs / "notice.pdf").write_bytes(make_pdf([TEXT]))
    (inputs / "specifications.pdf").write_bytes(
        make_pdf([TEXT + " Anforderungen an die Leistung."])
    )
    (inputs / "other.pdf").write_bytes(make_pdf([TEXT + " Andere Ausschreibung."]))
    config = MetadataFile(
        defaults=metadata(),
        files={
            "specifications.pdf": {"source_urls": ["https://example.invalid/specifications.pdf"]},
            "other.pdf": {"tender_id": "generated-tender-b"},
        },
    )
    register_directory(inputs, corpus, metadata=config)
    stored = manifest(corpus)
    assert len(stored["documents"]) == 3
    assert len(stored["tenders"]["generated-tender-a"]) == 2
    assert len(stored["tenders"]["generated-tender-b"]) == 1
    spec = next(
        doc
        for doc in stored["documents"]
        if doc["sources"][0]["original_filename"] == "specifications.pdf"
    )
    assert spec["sources"][0]["source_urls"] == ["https://example.invalid/specifications.pdf"]


def test_hash_dedup_retains_names_sources_and_tender_associations(folders):
    inputs, corpus = folders
    content = make_pdf([TEXT])
    (inputs / "notice.pdf").write_bytes(content)
    (inputs / "renamed.pdf").write_bytes(content)
    config = MetadataFile(
        defaults=metadata(),
        files={
            "renamed.pdf": {
                "tender_id": "generated-tender-b",
                "source_urls": ["https://example.invalid/second-source.pdf"],
            },
        },
    )
    run = register_directory(inputs, corpus, metadata=config)
    assert [result["status"] for result in run["results"]] == ["imported", "deduplicated"]
    stored = manifest(corpus)
    assert len(stored["documents"]) == len(list((corpus / "pdfs").glob("*.pdf"))) == 1
    assert [source["original_filename"] for source in stored["documents"][0]["sources"]] == [
        "notice.pdf",
        "renamed.pdf",
    ]
    assert stored["tenders"]["generated-tender-a"] == stored["tenders"]["generated-tender-b"]
    assert len(stored["documents"][0]["sources"]) == 2


def test_repeat_import_is_stable_and_never_overwrites_provenance(folders):
    inputs, corpus = folders
    (inputs / "notice.pdf").write_bytes(make_pdf([TEXT]))
    config = MetadataFile(defaults=metadata())
    register_directory(inputs, corpus, metadata=config)
    original = manifest(corpus)
    second = register_directory(inputs, corpus, metadata=config)
    assert second["results"][0]["status"] == "deduplicated"
    assert manifest(corpus)["documents"] == original["documents"]
    assert manifest(corpus)["tenders"] == original["tenders"]
    assert len(manifest(corpus)["import_runs"]) == 2
    updated = MetadataFile(
        defaults=metadata(rights_notes="Updated curator note, original retained.")
    )
    register_directory(inputs, corpus, metadata=updated)
    sources = manifest(corpus)["documents"][0]["sources"]
    assert len(sources) == 2 and sources[0]["rights_notes"] == metadata()["rights_notes"]


def test_changed_bytes_under_same_name_register_a_new_version(folders):
    inputs, corpus = folders
    file = inputs / "notice.pdf"
    file.write_bytes(make_pdf([TEXT]))
    register_directory(inputs, corpus)
    before = manifest(corpus)["documents"][0]
    file.write_bytes(make_pdf([TEXT + " Aktualisierte Fassung der Ausschreibung."]))
    register_directory(inputs, corpus)
    records = manifest(corpus)["documents"]
    assert len(records) == 2 and len({record["document_id"] for record in records}) == 2
    assert before in records and all(
        record["sources"][0]["original_filename"] == "notice.pdf" for record in records
    )


def test_missing_unverified_information_is_explicit_and_ted_is_optional(folders, caplog):
    inputs, corpus = folders
    (inputs / "notice.pdf").write_bytes(make_pdf([TEXT]))
    assert main([str(inputs), "--corpus-dir", str(corpus)]) == 0
    source = manifest(corpus)["documents"][0]["sources"][0]
    assert source["ted_publication_id"] is None and source["retrieval_date"] is None
    assert {
        "source_urls_missing",
        "retrieval_date_missing",
        "provenance_notes_missing",
        "rights_notes_missing",
        "provenance_unverified",
        "rights_unverified",
    } <= set(source["flags"])
    assert "rights_unverified" in caplog.text
    assert "gold_labels" not in manifest(corpus) and "scores" not in manifest(corpus)


@pytest.mark.parametrize(
    "fields",
    [
        {"provenance_status": "verified"},
        {"rights_status": "verified"},
        {"ted_publication_id": "not-a-TED-id"},
        {"retrieval_date": "not-a-date"},
        {"source_urls": ["file:///tmp/notice.pdf"]},
        {"source_urls": ["https://user:do-not-log@example.invalid/a"]},
        {"unknown_field": "value"},
    ],
)
def test_invalid_metadata_rejected_with_manifest_errors(folders, fields):
    inputs, corpus = folders
    (inputs / "notice.pdf").write_bytes(make_pdf([TEXT]))
    run = register_directory(inputs, corpus, metadata=MetadataFile(files={"notice.pdf": fields}))
    assert run["status"] == "partial" and run["results"][0]["status"] == "rejected"
    assert "Invalid metadata" in run["results"][0]["error"]
    assert "do-not-log" not in run["results"][0]["error"]
    assert manifest(corpus)["documents"] == []


def test_verified_defaults_can_use_per_file_provenance(folders):
    inputs, corpus = folders
    (inputs / "notice.pdf").write_bytes(make_pdf([TEXT]))
    values = metadata()
    defaults = {key: values.pop(key) for key in ["provenance_status", "rights_status"]}
    run = register_directory(
        inputs, corpus, metadata=MetadataFile(defaults=defaults, files={"notice.pdf": values})
    )
    assert run["status"] == "completed"


@pytest.mark.parametrize(
    "content,expected",
    [
        (b"not a PDF", "Could not parse PDF"),
        (make_pdf([""]), "Insufficient meaningful text"),
        (make_pdf(["short title"]), "Insufficient meaningful text"),
    ],
)
def test_reject_invalid_or_scanned_pdf_without_copying(folders, content, expected):
    inputs, corpus = folders
    (inputs / "bad.pdf").write_bytes(content)
    run = register_directory(inputs, corpus, metadata=MetadataFile(defaults=metadata()))
    result = run["results"][0]
    assert result["status"] == "rejected" and expected in result["error"]
    assert result["sha256"] == hashlib.sha256(content).hexdigest()
    assert result["validation"]["status"] == "failed"
    assert result["source"]["provenance_notes"] == metadata()["provenance_notes"]
    assert manifest(corpus)["documents"] == [] and not list(corpus.rglob("*.pdf"))


def test_encrypted_pdf_rejected(folders):
    inputs, corpus = folders
    writer = PdfWriter(clone_from=PdfReader(BytesIO(make_pdf([TEXT]))))
    writer.encrypt("generated-test-password")
    writer.write(inputs / "encrypted.pdf")
    run = register_directory(inputs, corpus)
    assert "Encrypted PDFs" in run["results"][0]["error"]
    assert manifest(corpus)["documents"] == []


def test_unreadable_pdf_and_mixed_import_preserve_success(folders, monkeypatch):
    inputs, corpus = folders
    unreadable = inputs / "unreadable.pdf"
    unreadable.write_bytes(make_pdf([TEXT]))
    (inputs / "valid.pdf").write_bytes(make_pdf([TEXT]))
    original_open = Path.open

    def guarded_open(path, *args, **kwargs):
        if path == unreadable:
            raise PermissionError("Read permission denied for generated fixture")
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", guarded_open)
    run = register_directory(inputs, corpus)
    assert run["status"] == "partial"
    assert "Read permission denied" in run["results"][0]["error"]
    assert run["results"][1]["status"] == "imported" and len(manifest(corpus)["documents"]) == 1


def test_oversized_pdf_rejected(folders, monkeypatch):
    inputs, corpus = folders
    (inputs / "large.pdf").write_bytes(make_pdf([TEXT]))
    monkeypatch.setattr(local_corpus, "MAX_DOCUMENT_BYTES", 10)
    run = register_directory(inputs, corpus)
    assert "25 MiB" in run["results"][0]["error"] and manifest(corpus)["documents"] == []


def test_hash_tampering_is_detected_and_not_silently_repaired(folders):
    inputs, corpus = folders
    (inputs / "notice.pdf").write_bytes(make_pdf([TEXT]))
    register_directory(inputs, corpus)
    record = manifest(corpus)["documents"][0]
    saved_manifest = (corpus / "manifest.json").read_bytes()
    target = corpus / record["path"]
    target.write_bytes(b"changed")
    assert "SHA-256/size mismatch" in verify_corpus(corpus, manifest(corpus))[0]
    assert main(["--verify", "--corpus-dir", str(corpus)]) == 1
    with pytest.raises(ValueError, match="integrity check failed"):
        register_directory(inputs, corpus)
    assert (
        target.read_bytes() == b"changed"
        and (corpus / "manifest.json").read_bytes() == saved_manifest
    )


def test_missing_pdf_and_manifest_path_mismatch_detected(folders):
    inputs, corpus = folders
    (inputs / "notice.pdf").write_bytes(make_pdf([TEXT]))
    register_directory(inputs, corpus)
    stored = manifest(corpus)
    file = corpus / stored["documents"][0]["path"]
    file.unlink()
    assert verify_corpus(corpus, stored)
    stored["documents"][0]["path"] = "../outside.pdf"
    assert "Unexpected corpus PDF path" in verify_corpus(corpus, stored)[0]


def test_cli_metadata_file_precedence_and_read_only_verification(folders):
    inputs, corpus = folders
    (inputs / "notice.pdf").write_bytes(make_pdf([TEXT]))
    config = corpus.parent / "metadata.json"
    config.write_text(
        json.dumps(
            {
                "defaults": metadata(tender_id="default"),
                "files": {"notice.pdf": {"tender_id": "per-file"}},
            }
        ),
        encoding="utf-8",
    )
    assert (
        main(
            [
                str(inputs),
                "--corpus-dir",
                str(corpus),
                "--metadata",
                str(config),
                "--tender-id",
                "cli",
                "--ted-publication-id",
                "123456-2026",
            ]
        )
        == 0
    )
    stored = manifest(corpus)
    assert list(stored["tenders"]) == ["per-file"]
    assert stored["documents"][0]["sources"][0]["ted_publication_id"] == "123456-2026"
    before = (corpus / "manifest.json").read_bytes()
    assert main(["--verify", "--corpus-dir", str(corpus)]) == 0
    assert (corpus / "manifest.json").read_bytes() == before


def test_empty_input_version_mismatch_and_metadata_typo_fail(folders):
    inputs, corpus = folders
    assert main([str(inputs), "--corpus-dir", str(corpus)]) == 1
    assert not corpus.exists()
    assert main(["--verify", "--corpus-dir", str(corpus)]) == 1
    (inputs / "notice.pdf").write_bytes(make_pdf([TEXT]))
    with pytest.raises(ValueError, match="filenames not present"):
        register_directory(inputs, corpus, metadata=MetadataFile(files={"typo.pdf": {}}))
    register_directory(inputs, corpus)
    with pytest.raises(ValueError, match="version mismatch"):
        load_corpus(corpus, "other-version")


def test_pdf_symlinks_are_not_imported(folders):
    inputs, corpus = folders
    external = inputs.parent / "external.pdf"
    external.write_bytes(make_pdf([TEXT]))
    (inputs / "linked.pdf").symlink_to(external)
    run = register_directory(inputs, corpus)
    assert "symlinks are not supported" in run["results"][0]["error"]
    assert manifest(corpus)["documents"] == []


def test_git_ignores_default_and_custom_pdf_locations():
    root = Path(__file__).resolve().parents[1]
    paths = [
        "data/evaluation/manual-input/notice.pdf",
        "data/evaluation/real-pdf-1/manifest.json",
        "evaluation/custom-corpus/pdfs/example.pdf",
        "custom-input/NOTICE.PDF",
        "elsewhere/mixed.PdF",
    ]
    result = subprocess.run(
        ["git", "check-ignore", "--no-index", "--stdin"],
        cwd=root,
        input="\n".join(paths) + "\n",
        text=True,
        capture_output=True,
        check=True,
    )
    assert set(result.stdout.splitlines()) == set(paths)
    tracked = subprocess.run(
        ["git", "ls-files", "--", "*.[pP][dD][fF]"],
        cwd=root,
        text=True,
        capture_output=True,
        check=True,
    )
    assert tracked.stdout == ""


def test_cli_rejections_are_nonzero_and_name_the_input(folders, caplog):
    inputs, corpus = folders
    (inputs / "broken.pdf").write_bytes(b"not a PDF")
    (inputs / "valid.pdf").write_bytes(make_pdf([TEXT]))
    assert main([str(inputs), "--corpus-dir", str(corpus)]) == 1
    assert "broken.pdf: Could not parse PDF" in caplog.text
    assert len(manifest(corpus)["documents"]) == 1


def test_malformed_manifest_and_empty_corpus_cannot_claim_verified(folders, caplog):
    inputs, corpus = folders
    corpus.mkdir()
    (corpus / "manifest.json").write_text("[]", encoding="utf-8")
    assert main(["--verify", "--corpus-dir", str(corpus)]) == 1
    assert "Malformed corpus manifest" in caplog.text
    (corpus / "manifest.json").unlink()
    (inputs / "broken.pdf").write_bytes(b"invalid PDF")
    register_directory(inputs, corpus)
    assert main(["--verify", "--corpus-dir", str(corpus)]) == 1
    assert "no registered PDFs" in caplog.text
