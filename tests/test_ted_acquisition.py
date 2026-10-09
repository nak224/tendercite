"""Offline acquisition through MockTransport, with real generated PDFs and pypdf parsing."""

import json

import httpx
import pytest

from evaluation.acquire_ted import parser, run
from evaluation.generate import make_pdf
from evaluation.ted_acquisition import (
    acquire_notice,
    check_pdf,
    inspect_xml,
    load_manifest,
    save_manifest,
    verified_cache,
)
from tendercite.services.ted import TedClient

TEXT = (
    "Für den Betrieb der Software sind zwei Referenzen vorzulegen. "
    "Die Angebotsfrist endet am angegebenen Datum. Der Auftrag umfasst technische "
    "Unterstützung, Wartung und die Bereitstellung geeigneter Fachkräfte."
)
XML = b"""<ContractNotice xmlns="urn:oasis:names:specification:ubl:schema:xsd:ContractNotice-2"
 xmlns:cbc="urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2"
 xmlns:cac="urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2">
 <cbc:NoticeLanguageCode>deu</cbc:NoticeLanguageCode>
 <cbc:CustomizationID>eforms-sdk-test-only</cbc:CustomizationID>
 <cac:TenderingTerms><cbc:TenderLanguageCode>eng</cbc:TenderLanguageCode></cac:TenderingTerms>
</ContractNotice>"""


def payload(notice_id="123456-2026", language="DEU"):
    return {
        "publication-number": notice_id,
        "notice-title": {"deu": "Softwarebetrieb"},
        "official-language": [language],
        "publication-date": "2026-07-01+02:00",
        "form-type": {"value": "competition"},
        "links": {
            "pdf": {"DEU": f"https://ted.europa.eu/de/notice/{notice_id}/pdf"},
            "xml": {"MUL": f"https://ted.europa.eu/en/notice/{notice_id}/xml"},
        },
    }


def fake_api(*, raw=None, xml=XML, pdf=None):
    requests = []
    raw = payload() if raw is None else raw
    pdf = make_pdf([TEXT]) if pdf is None else pdf

    def handler(request):
        requests.append(request)
        if request.method == "POST":
            return httpx.Response(200, json={"notices": [raw], "totalNoticeCount": 1})
        return httpx.Response(200, content=xml if request.url.path.endswith("/xml") else pdf)

    client = TedClient(httpx.Client(transport=httpx.MockTransport(handler)), sleep=lambda _: None)
    return client, requests


def test_acquisition_provenance_real_pdf_parse_and_hash_deduplication(tmp_path):
    client, requests = fake_api()
    notice = client.lookup("123456-2026")
    record = acquire_notice(client, notice, tmp_path)
    assert record["status"] == "acquired" and record["shortlist_eligible"]
    assert record["annotation_status"] == "not_annotated" and record["manual_review"] == "pending"
    assert not record["failures"]
    assert len(record["documents"]) == 2
    for doc in record["documents"]:
        assert (
            doc["sha256"]
            and doc["retrieved_at"]
            and doc["source_url"].startswith("https://ted.europa.eu/")
        )
        assert (tmp_path / doc["path"]).is_file()
        assert doc["validation"]["status"] == "passed"
    pdf = record["documents"][1]
    assert pdf["validation"]["pages"] == 1 and pdf["validation"]["words"] >= 20
    assert verified_cache(record, tmp_path)
    # Different publication IDs containing identical bytes share the same local files.
    second, _ = fake_api(raw=payload("654321-2026"))
    duplicate = acquire_notice(second, second.lookup("654321-2026"), tmp_path)
    assert all(doc["content_reused"] for doc in duplicate["documents"])
    assert len(list((tmp_path / "documents").iterdir())) == 2
    assert len(requests) == 3  # metadata + XML + PDF; no third-party attachments
    (tmp_path / pdf["path"]).write_bytes(b"tampered")
    assert not verified_cache(record, tmp_path)


def test_translated_german_link_does_not_establish_original_german(tmp_path):
    client, requests = fake_api(raw=payload(language="ENG"))
    record = acquire_notice(client, client.lookup("123456-2026"), tmp_path)
    assert record["status"] == "skipped_language"
    assert record["documents"] == [] and len(requests) == 1
    raw = payload()
    del raw["official-language"]
    client, requests = fake_api(raw=raw)
    record = acquire_notice(client, client.lookup("123456-2026"), tmp_path)
    assert record["status"] == "skipped_unknown_language" and len(requests) == 1


def test_conflicting_api_xml_language_skips_pdf(tmp_path):
    client, requests = fake_api(xml=XML.replace(b">deu<", b">eng<"))
    record = acquire_notice(client, client.lookup("123456-2026"), tmp_path)
    assert record["status"] == "language_conflict" and not record["shortlist_eligible"]
    assert "API/XML original-language metadata disagree" in record["failures"]
    assert len(requests) == 2


def test_xml_original_language_fields_and_legacy_translation_separation():
    result = inspect_xml(XML)
    assert result["original_languages"] == ["deu"]
    assert result["customization_id"] == "eforms-sdk-test-only" and not result["schema_validated"]
    multilingual = XML.replace(
        b"</ContractNotice>",
        b"""<cac:AdditionalNoticeLanguage>
        <cbc:ID>eng</cbc:ID></cac:AdditionalNoticeLanguage></ContractNotice>""",
    )
    assert inspect_xml(multilingual)["original_languages"] == ["deu", "eng"]
    legacy = b"""<TED_EXPORT><FORM_SECTION><F02 LG="DE" CATEGORY="ORIGINAL"/>
        <F02 LG="EN" CATEGORY="TRANSLATION"/></FORM_SECTION></TED_EXPORT>"""
    assert inspect_xml(legacy)["original_languages"] == ["deu"]
    assert (
        inspect_xml(XML.replace(b"<cbc:NoticeLanguageCode>deu</cbc:NoticeLanguageCode>", b""))[
            "language_status"
        ]
        == "missing"
    )


@pytest.mark.parametrize(
    "content", [b"<broken>", b"<html/>", b'<!DOCTYPE a [<!ENTITY b "x">]><a>&b;</a>', b"\xff\xfe"]
)
def test_unusable_xml_is_recorded(content):
    assert inspect_xml(content)["status"] == "failed"


@pytest.mark.parametrize("pdf", [b"not PDF", make_pdf([""]), make_pdf(["title only"])])
def test_scans_and_malformed_pdfs_fail_and_are_logged(tmp_path, caplog, pdf):
    client, _ = fake_api(pdf=pdf)
    record = acquire_notice(client, client.lookup("123456-2026"), tmp_path)
    assert record["status"] == "unusable_pdf" and not record["shortlist_eligible"]
    assert record["documents"][1]["validation"]["status"] == "failed"
    assert record["failures"] and "123456-2026" in caplog.text


def test_encrypted_pdf_is_rejected(tmp_path):
    from io import BytesIO

    from pypdf import PdfReader, PdfWriter

    writer = PdfWriter(clone_from=PdfReader(BytesIO(make_pdf([TEXT]))))
    writer.encrypt("secret")
    path = tmp_path / "encrypted.pdf"
    writer.write(path)
    assert check_pdf(path).reason == "Encrypted PDFs are not supported"


def test_missing_formats_and_http_download_failures_are_retained(tmp_path):
    raw = payload()
    raw["links"] = {}
    client, _ = fake_api(raw=raw)
    record = acquire_notice(client, client.lookup("123456-2026"), tmp_path)
    assert len(record["failures"]) == 2 and record["status"] == "pdf_not_obtained"

    def handler(request):
        if request.method == "POST":
            return httpx.Response(200, json={"notices": [payload()], "totalNoticeCount": 1})
        return httpx.Response(404)

    client = TedClient(httpx.Client(transport=httpx.MockTransport(handler)), sleep=lambda _: None)
    record = acquire_notice(client, client.lookup("123456-2026"), tmp_path)
    assert len(record["failures"]) == 2 and all("HTTP 404" in error for error in record["failures"])
    assert record["pdf_validation_status"] == "not_attempted"
    assert all(attempt["status"] == "failed" for attempt in record["download_attempts"])


def test_signed_pdf_available_without_unsigned_pdf(tmp_path):
    raw = payload()
    raw["links"]["pdfs"] = {"DEU": "https://ted.europa.eu/de/notice/123456-2026/pdfs"}
    del raw["links"]["pdf"]
    client, _ = fake_api(raw=raw)
    record = acquire_notice(client, client.lookup("123456-2026"), tmp_path)
    assert record["shortlist_eligible"] and record["documents"][1]["format"] == "pdfs"
    assert record["documents"][1]["path"].endswith(".pdf")


def test_cli_fixed_ids_cache_refresh_shortlist_and_summary(tmp_path):
    args = parser().parse_args(
        [
            "--output-dir",
            str(tmp_path),
            "--notice-id",
            "00123456-2026",
            "--notice-id",
            "123456-2026",
            "--shortlist-id",
            "123456-2026",
            "--summary",
            str(tmp_path / "summary.json"),
        ]
    )
    client, requests = fake_api()
    assert run(args, client) == 0
    manifest = json.loads((tmp_path / "manifest.json").read_text())
    assert len(manifest["notices"]) == 1 and manifest["shortlist"] == ["123456-2026"]
    assert manifest["gold_labels"] is None and manifest["dataset_version"] == "ted-pilot-1"
    assert manifest["runs"][0]["queries"] == ['publication-number = "123456-2026"']
    assert json.loads((tmp_path / "summary.json").read_text()) == manifest
    assert len(requests) == 3
    assert run(args, client) == 0 and len(requests) == 4  # metadata only on a verified cache hit
    args.refresh = True
    assert run(args, client) == 0 and len(requests) == 7
    assert len(json.loads((tmp_path / "manifest.json").read_text())["notices"]) == 1


def test_failed_live_attempt_is_nonzero_and_preserves_prior_manifest(tmp_path):
    manifest = load_manifest(tmp_path, "ted-pilot-1")
    manifest["notices"] = [{"publication_id": "123456-2026", "documents": []}]
    save_manifest(tmp_path, manifest)
    args = parser().parse_args(["--output-dir", str(tmp_path), "--date-from", "2026-07-01"])

    def handler(request):
        raise httpx.ProxyError("403 Forbidden", request=request)

    client = TedClient(httpx.Client(transport=httpx.MockTransport(handler)), sleep=lambda _: None)
    assert run(args, client) == 1
    manifest = json.loads((tmp_path / "manifest.json").read_text())
    assert len(manifest["notices"]) == 1
    last = manifest["runs"][-1]
    assert last["status"] == "failed" and "ProxyError" in last["failures"][0]
    assert "total_notice_count" not in last  # failure is not represented as zero TED matches
    assert last["filters"]["date_from"] == "2026-07-01"


def test_cli_unusable_pdf_is_partial_not_success(tmp_path):
    client, _ = fake_api(pdf=make_pdf([""]))
    args = parser().parse_args(["--output-dir", str(tmp_path)])
    assert run(args, client) == 1
    last = json.loads((tmp_path / "manifest.json").read_text())["runs"][-1]
    assert last["status"] == "partial" and last["usable_pdf_count"] == 0
    assert "123456-2026" in last["document_failures"]


def test_cli_smoke_only_requests_metadata(tmp_path):
    client, requests = fake_api()
    assert run(parser().parse_args(["--output-dir", str(tmp_path), "--smoke"]), client) == 0
    assert len(requests) == 1
    assert json.loads((tmp_path / "manifest.json").read_text())["notices"] == []


def test_dataset_version_conflict_is_not_overwritten(tmp_path):
    save_manifest(tmp_path, load_manifest(tmp_path, "ted-pilot-1"))
    with pytest.raises(ValueError, match="separate directory"):
        load_manifest(tmp_path, "ted-pilot-2")


def test_manifest_cache_cannot_read_outside_dataset_directory(tmp_path):
    record = {
        "status": "acquired",
        "failures": [],
        "documents": [{"path": "../outside.pdf", "sha256": "fake"}] * 2,
    }
    assert not verified_cache(record, tmp_path)


def test_shortlist_requires_usable_original_pdf(tmp_path):
    client, _ = fake_api(pdf=make_pdf([""]))
    assert (
        run(
            parser().parse_args(["--output-dir", str(tmp_path), "--shortlist-id", "123456-2026"]),
            client,
        )
        == 1
    )
    manifest = json.loads((tmp_path / "manifest.json").read_text())
    assert manifest["shortlist"] == [] and len(manifest["notices"]) == 1
    assert "passed PDF" in manifest["runs"][-1]["failures"][0]


def test_smoke_does_not_retest_prior_failed_documents(tmp_path):
    client, requests = fake_api(pdf=make_pdf([""]))
    args = parser().parse_args(["--output-dir", str(tmp_path)])
    assert run(args, client) == 1
    args.smoke = True
    assert run(args, client) == 0 and len(requests) == 4
