"""Original synthetic QA/PDFs and mocked SDK/HTTP failures; never download in CI."""

import hashlib
import json
import sys
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from pypdf import PdfReader, PdfWriter

from evaluation import external_benchmark as pilot
from evaluation.acquire_external_benchmark import main
from evaluation.external_qa import chunk_document, validate_metadata
from evaluation.generate import make_pdf

REVISION = "a" * 40
FAMILY = "Generated_Family"
TEXT = (
    "The supplier must provide two references for comparable software projects. "
    "This original synthetic notice describes a procurement procedure and its requirements. "
    "The supporting documents contain administrative details and illustrative background text."
)


def row(**changes):
    return {
        "id": "generated-lookup",
        "family": FAMILY,
        "question": "How many references?",
        "answer": "Two comparable references.",
        "question_type": "Single-document lookup",
        "type_number": 1,
        "hop_count": 1,
        "difficulty": "easy",
        "relevant_chunks": ["notice.pdf_003"],
        "source_documents": ["notice.pdf"],
        "documents_in_family": ["notice.pdf", "contract.pdf"],
        "reasoning_note": "Original synthetic candidate; not independently verified gold.",
        **changes,
    }


class FakeHub:
    def __init__(self, rows=None):
        self.data = {
            "README.md": b"Original synthetic card; no third-party content.",
            pilot.QA_FILENAME: json.dumps([row()] if rows is None else rows).encode(),
            f"data/{FAMILY}/notice.pdf": make_pdf([TEXT]),
            f"data/{FAMILY}/contract.pdf": make_pdf([TEXT + " A contract annex.", TEXT]),
            "data/Other_Family/unrequested.pdf": make_pdf([TEXT]),
        }
        self.calls = []
        self.failures = {}
        self.revision = REVISION

    def describe(self, revision):
        return {
            "revision": self.revision,
            "card": {"license": "mit", "language": ["en"]},
            "files": [
                {
                    "filename": name,
                    "size": len(data),
                    "upstream_sha256": hashlib.sha256(data).hexdigest(),
                    "git_blob_sha1": None,
                }
                for name, data in self.data.items()
            ],
        }

    def download(self, filename, revision, staging):
        self.calls.append((filename, revision))
        if filename in self.failures:
            raise self.failures[filename]
        target = staging / filename
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(self.data[filename])
        return target


@pytest.fixture
def directory(tmp_path, monkeypatch):
    root = tmp_path / "external"
    monkeypatch.setattr(pilot, "EXTERNAL_ROOT", root)
    return root / "pilot"


def acquire(directory, hub=None, **kwargs):
    return pilot.acquire(
        REVISION, directory, family=FAMILY, download_pdfs=True, source=hub or FakeHub(), **kwargs
    )


def test_actual_parser_one_family_document_references_without_invented_spans(directory):
    hub = FakeHub()
    result = acquire(directory, hub)
    assert result["status"] == "validated"
    assert result["metadata"]["records"] == 1
    selected = result["family_validation"]
    assert selected["usable_document_reference_questions"] == 1
    assert selected["exact_source_span_ground_truth_questions"] == 0
    assert selected["independently_human_verified_questions"] == 0
    assert not selected["retrieval_benchmark_ready"] and result["retrieval_scores"] is None
    assert result["label_origin"] == "upstream_candidates_not_independent_human_gold"
    assert len(selected["pdfs"]) == 2 and sorted(
        pdf["pages"] for pdf in selected["pdfs"].values()
    ) == [1, 2]
    assert all(FAMILY in name or name in ["README.md", pilot.QA_FILENAME] for name, _ in hub.calls)
    assert all(revision == REVISION for _, revision in hub.calls)
    mapping = selected["cases"][0]["chunk_references"][0]
    assert mapping["repository_file"] == f"data/{FAMILY}/notice.pdf"
    assert mapping["page_number"] is None and mapping["source_span"] is None
    assert not mapping["original_chunk_reconstructed"]
    report_text = json.dumps(result)
    assert TEXT not in report_text and row()["answer"] not in report_text


def test_metadata_only_and_offline_validation_never_request_pdfs_or_network(directory, monkeypatch):
    hub = FakeHub()
    result = pilot.acquire("main", directory, family=FAMILY, source=hub)
    assert result["revision"] == REVISION and result["status"] == "validated"
    assert [name for name, _ in hub.calls] == ["README.md", pilot.QA_FILENAME]
    assert not result["pdf_downloads_requested"]
    assert result["family_validation"]["usable_document_reference_questions"] == 0
    monkeypatch.setattr(
        pilot, "HuggingFaceSource", lambda _: pytest.fail("Live source invoked offline")
    )
    assert main(["--validate-only", "--output-dir", str(directory)]) == 0


def test_repeat_acquisition_preserves_hashes_dates_and_previously_acquired_pdfs(directory):
    hub = FakeHub()
    first = acquire(directory, hub)
    first_files = first["acquisition_files"]
    hub.calls.clear()
    second = acquire(directory, hub)
    assert second["acquisition_files"] == first_files and hub.calls == []
    third = pilot.acquire(REVISION, directory, family=FAMILY, source=hub)
    assert third["acquisition_files"] == first_files
    assert third["family_validation"]["usable_document_reference_questions"] == 1
    assert all(item["source_repository"] == pilot.REPOSITORY for item in first_files)
    assert all(item["revision"] == REVISION and len(item["sha256"]) == 64 for item in first_files)


@pytest.mark.parametrize(
    "changes,reason",
    [
        ({"answer": ""}, "answer"),
        ({"question": " "}, "must not be blank"),
        ({"question_type": "Unsupported"}, "question_type"),
        ({"type_number": 2}, "contradict"),
        ({"hop_count": True}, "hop_count"),
        ({"difficulty": "impossible"}, "difficulty"),
        ({"source_documents": ["unknown.pdf"]}, "outside documents_in_family"),
        ({"relevant_chunks": ["unknown.pdf_003"]}, "outside source_documents"),
        ({"relevant_chunks": ["opaque-chunk-id"]}, "Unsupported relevant_chunks"),
        ({"relevant_chunks": []}, "no document/chunk evidence"),
        ({"documents_in_family": ["notice.pdf", "missing.pdf"]}, "absent from repository"),
        ({"unknown_field": "ignored?"}, "Extra inputs"),
    ],
)
def test_metadata_validation_keeps_explicit_rejections(changes, reason):
    _, summary = validate_metadata(
        [row(**changes)], {f"data/{FAMILY}/notice.pdf", f"data/{FAMILY}/contract.pdf"}
    )
    assert summary["valid_records"] == 0
    assert reason in " ".join(summary["invalid_records"][0]["errors"])


def test_duplicates_empty_and_unsupported_metadata_are_reported(directory):
    valid, summary = validate_metadata(
        [row(), row()], {f"data/{FAMILY}/notice.pdf", f"data/{FAMILY}/contract.pdf"}
    )
    assert not valid and len(summary["invalid_records"]) == 2
    assert all("Duplicate QA ID" in failure["errors"] for failure in summary["invalid_records"])
    empty = acquire(directory, FakeHub([]))
    assert empty["status"] == "failed" and empty["metadata"]["records"] == 0
    with pytest.raises(ValueError, match="array"):
        validate_metadata({}, set())
    with pytest.raises(ValueError, match="at most 1000"):
        validate_metadata([row()] * 1001, set())


def test_unanswerable_is_separate_and_contradictory_evidence_is_flagged(directory):
    questions = [
        row(),
        row(
            id="abstention",
            question_type="Unanswerable",
            type_number=5,
            hop_count=0,
            source_documents=[],
            relevant_chunks=[],
        ),
        row(id="needs-review", question_type="Unanswerable", type_number=5),
    ]
    result = acquire(directory, FakeHub(questions))
    selected = result["family_validation"]
    assert selected["questions"] == 3 and selected["unanswerable"] == 2
    assert selected["usable_document_reference_questions"] == 1
    assert result["metadata"]["metadata_review_flags"] == [
        {
            "id": "needs-review",
            "reason": "Unanswerable case has evidence references or nonzero hops",
        }
    ]
    assert all(case["human_review_required"] for case in selected["cases"])


def test_missing_pdf_and_failed_download_do_not_become_zero_result_success(directory):
    hub = FakeHub()
    hub.data.pop(f"data/{FAMILY}/notice.pdf")
    result = acquire(directory, hub)
    assert result["status"] == "failed" and result["metadata"]["invalid_records"]
    assert "absent from repository" in json.dumps(result["metadata"])


def test_http_failure_reports_host_proxy_status_and_strips_signed_queries(directory):
    hub = FakeHub()
    request = httpx.Request("GET", "https://blocked.hf.co/file?signature=do-not-log")
    response = httpx.Response(403, text="DOMAIN_NOT_ALLOWED by proxy", request=request)
    hub.failures[f"data/{FAMILY}/notice.pdf"] = httpx.HTTPStatusError(
        "blocked by proxy: https://blocked.hf.co/file?signature=do-not-log",
        request=request,
        response=response,
    )
    result = acquire(directory, hub)
    assert result["status"] == "failed"
    failure = next(item for item in result["acquisition_files"] if item["status"] == "failed")
    assert failure["hostname"] == "blocked.hf.co" and failure["http_status"] == 403
    assert failure["proxy_denial_observed"]
    assert "do-not-log" not in json.dumps(result)
    assert result["family_validation"]["usable_document_reference_questions"] == 0


def test_metadata_api_failure_is_explicit_and_preserved(directory):
    class FailedSource:
        def describe(self, revision):
            raise RuntimeError("connection failed at https://huggingface.co/api/datasets/example")

    report = pilot.acquire(REVISION, directory, source=FailedSource())
    assert report["status"] == "acquisition_failed"
    assert report["failures"][0]["hostname"] == "huggingface.co"
    assert (directory / "acquisition-failure.json").is_file()
    assert not (directory / "manifest.json").exists()


@pytest.mark.parametrize("corruption", ["bytes", "missing", "manifest_hash", "git_blob"])
def test_checksum_and_missing_file_checks_do_not_overwrite(directory, corruption):
    acquire(directory)
    manifest_path = directory / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    record = next(
        item for item in manifest["files"] if item["filename"] == f"data/{FAMILY}/notice.pdf"
    )
    target = directory / record["path"]
    if corruption == "bytes":
        target.write_bytes(b"changed")
    elif corruption == "missing":
        target.unlink()
    elif corruption == "manifest_hash":
        record["sha256"] = "0" * 64
    else:
        spec = next(
            item for item in manifest["inventory"] if item["filename"] == record["filename"]
        )
        spec["git_blob_sha1"] = "0" * 40
    manifest_path.write_text(json.dumps(manifest))
    result = pilot.validate_local(directory)
    assert result["status"] == "failed" and result["issues"]
    if corruption == "bytes":
        acquire(directory)
        assert target.read_bytes() == b"changed"
    if corruption == "missing":
        acquire(directory)
        assert not target.exists()


@pytest.mark.parametrize(
    "unsafe", ["../escape.pdf", "/absolute.pdf", "a\\b.pdf", "C:drive.pdf", "CON.pdf"]
)
def test_unsafe_metadata_names_are_rejected(unsafe):
    _, summary = validate_metadata(
        [row(source_documents=[unsafe], documents_in_family=[unsafe])], set()
    )
    assert "Unsafe" in json.dumps(summary["invalid_records"])
    with pytest.raises(ValueError, match="Unsafe"):
        chunk_document(unsafe + "_001")


def test_output_repository_paths_and_symlinks_cannot_escape_ignored_root(directory, tmp_path):
    with pytest.raises(ValueError, match="ignored"):
        acquire(tmp_path / "outside")
    directory.mkdir(parents=True)
    (directory / "artifacts").symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(ValueError, match="ignored|Symlinks"):
        acquire(directory)


def test_malformed_pdf_is_acquired_but_parsing_failure_is_explicit(directory):
    hub = FakeHub()
    hub.data[f"data/{FAMILY}/notice.pdf"] = b"malformed synthetic PDF"
    result = acquire(directory, hub)
    assert result["status"] == "failed"
    check = result["family_validation"]["pdfs"][f"data/{FAMILY}/notice.pdf"]
    assert check["status"] == "failed" and "Could not parse PDF" in check["reason"]


def test_encrypted_pdf_keeps_partial_document_reference_coverage_without_bypass(directory):
    hub = FakeHub(
        [
            row(),
            row(
                id="contract-question",
                source_documents=["contract.pdf"],
                relevant_chunks=["contract.pdf_003"],
            ),
        ]
    )
    name = f"data/{FAMILY}/notice.pdf"
    writer = PdfWriter(clone_from=PdfReader(BytesIO(hub.data[name])))
    writer.encrypt("original-fixture-password")
    buffer = BytesIO()
    writer.write(buffer)
    hub.data[name] = buffer.getvalue()
    result = acquire(directory, hub)
    assert result["status"] == "failed"
    assert all(item["status"] == "downloaded" for item in result["acquisition_files"])
    selected = result["family_validation"]
    assert selected["usable_document_reference_questions"] == 1
    assert "Encrypted PDFs" in selected["pdfs"][name]["reason"]
    assert [case["document_references_usable"] for case in selected["cases"]] == [False, True]
    assert selected["exact_source_span_ground_truth_questions"] == 0


def test_revision_family_limits_and_sdk_staging_escape(directory):
    hub = FakeHub()
    acquire(directory, hub)
    hub.revision = "b" * 40
    with pytest.raises(ValueError, match="another revision/family"):
        acquire(directory, hub)
    with pytest.raises(ValueError, match="exactly one"):
        pilot.acquire(REVISION, directory, download_pdfs=True, source=hub)
    hub.revision = "mutable"
    with pytest.raises(ValueError, match="immutable"):
        acquire(directory, hub)
    clean = directory.parent / "other"
    hub = FakeHub()
    hub.download = lambda *args: Path(__file__)
    report = acquire(clean, hub)
    assert report["status"] == "failed" and "escaped staging" in json.dumps(report)


def test_bounds_for_document_count_and_size(directory):
    hub = FakeHub()
    for index in range(33):
        hub.data[f"data/{FAMILY}/extra-{index}.pdf"] = b"small original fixture"
    with pytest.raises(ValueError, match="32 PDFs"):
        acquire(directory, hub)
    hub = FakeHub()
    describe = hub.describe

    def oversized(revision):
        result = describe(revision)
        next(item for item in result["files"] if item["filename"] == pilot.QA_FILENAME)["size"] = (
            pilot.METADATA_LIMIT + 1
        )
        return result

    hub.describe = oversized
    result = acquire(directory, hub)
    assert result["status"] == "failed" and "oversized" in json.dumps(result)


def test_cli_and_manifest_tampering_fail_clearly(directory, monkeypatch):
    hub = FakeHub()
    monkeypatch.setattr(pilot, "HuggingFaceSource", lambda _: hub)
    args = [
        "--revision",
        REVISION,
        "--family",
        FAMILY,
        "--download-pdfs",
        "--output-dir",
        str(directory),
    ]
    assert main(args) == 0
    path = directory / "manifest.json"
    manifest = json.loads(path.read_text())
    manifest["files"][0]["path"] = "../escape"
    path.write_text(json.dumps(manifest))
    assert main(["--validate-only", "--output-dir", str(directory)]) == 1
    assert main(["--output-dir", str(directory)]) == 1
    assert main(["--validate-only", "--revision", REVISION, "--output-dir", str(directory)]) == 1
    path.write_text("[]")
    assert main(args) == 1


def test_official_sdk_is_used_with_pinned_revision_public_auth_and_local_caches(
    directory, monkeypatch
):
    calls = []
    monkeypatch.setenv("HF_XET_CACHE", str(directory / "initial"))

    def api(**kwargs):
        assert kwargs == {"token": False}

        def describe(repo, **kwargs):
            calls.append((repo, kwargs))
            return SimpleNamespace(sha=REVISION, card_data=None, siblings=[])

        return SimpleNamespace(dataset_info=describe)

    def download(**kwargs):
        calls.append(kwargs)
        return kwargs["local_dir"] / kwargs["filename"]

    monkeypatch.setitem(
        sys.modules,
        "huggingface_hub",
        SimpleNamespace(
            HfApi=api,
            constants=SimpleNamespace(HF_XET_CACHE=directory / ".xet-cache"),
            hf_hub_download=download,
        ),
    )
    source = pilot.HuggingFaceSource(directory)
    assert source.describe("main")["revision"] == REVISION
    source.download(pilot.QA_FILENAME, REVISION, directory / "staging")
    assert calls[0][1]["files_metadata"] and calls[0][1]["revision"] == "main"
    args = calls[1]
    assert (
        args["revision"] == REVISION and args["repo_type"] == "dataset" and args["token"] is False
    )
    assert args["cache_dir"].is_relative_to(directory)
    assert "verify" not in args and "endpoint" not in args
