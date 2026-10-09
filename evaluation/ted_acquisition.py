"""Local evaluation acquisition only. No automatic gold labels or production ingestion."""

import hashlib
import json
import logging
import re
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from xml.etree import ElementTree

from tendercite.services.pdf_parser import PdfParseError, PyPdfParser
from tendercite.services.ted import TedClient, TedError, TedNotice, language_code

logger = logging.getLogger(__name__)
DATASET_VERSION = "ted-pilot-1"
CBC = "urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2"
CAC = "urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2"
UBL_ROOTS = {"ContractNotice", "ContractAwardNotice", "PriorInformationNotice"}


def timestamp() -> str:
    return datetime.now(UTC).isoformat()


@dataclass(frozen=True)
class PdfCheck:
    status: str
    pages: int = 0
    text_characters: int = 0
    alphabetic_characters: int = 0
    words: int = 0
    reason: str | None = None


def check_pdf(path: Path) -> PdfCheck:
    try:
        pages = PyPdfParser().parse(path, max_pages=250, max_chars=1_000_000)
    except PdfParseError as exc:
        return PdfCheck("failed", reason=str(exc))
    text = "\n".join(page.text for page in pages)
    letters = sum(char.isalpha() for char in text)
    words = len(re.findall(r"\b[^\W\d_]{2,}\b", text))
    if letters < 100 or words < 20:
        return PdfCheck(
            "failed",
            len(pages),
            len(text),
            letters,
            words,
            "Insufficient meaningful text; possibly scanned or unusable (no OCR)",
        )
    return PdfCheck("passed", len(pages), len(text), letters, words)


def inspect_xml(content: bytes) -> dict:
    """Inspect known original-language fields, never UI/tender-submission languages."""
    try:
        text = content.decode("utf-8-sig")
        if "<!DOCTYPE" in text.upper() or "<!ENTITY" in text.upper():
            raise ValueError("DTD/entities are not supported")
        root = ElementTree.fromstring(text)
        name = root.tag.rsplit("}", 1)[-1]
        languages = set()
        if name in UBL_ROOTS and root.tag.startswith(
            f"{{urn:oasis:names:specification:ubl:schema:xsd:{name}-2}}"
        ):
            for element in root.findall(f"./{{{CBC}}}NoticeLanguageCode") + root.findall(
                f"./{{{CAC}}}AdditionalNoticeLanguage/{{{CBC}}}ID"
            ):
                if element.text:
                    languages.add(language_code(element.text.strip()))
        elif name == "TED_EXPORT":
            # Legacy original form language, not TRANSLATION_SECTION or arbitrary @LG.
            for section in root.findall(".//FORM_SECTION"):
                for form in section:
                    if form.get("CATEGORY") == "ORIGINAL" and form.get("LG"):
                        languages.add(language_code(form.attrib["LG"]))
        else:
            raise ValueError("Not a supported eForms UBL or legacy TED notice XML")
        return {
            "status": "passed",
            "root": name,
            "original_languages": sorted(languages),
            "language_status": "recorded" if languages else "missing",
            "schema_validated": False,
            "customization_id": root.findtext(f"./{{{CBC}}}CustomizationID"),
        }
    except (UnicodeError, ElementTree.ParseError, ValueError) as exc:
        return {
            "status": "failed",
            "reason": str(exc),
            "original_languages": [],
            "schema_validated": False,
        }


def _link(notice: TedNotice, formats: tuple[str, ...], language: str) -> tuple[str, str] | None:
    for format in formats:
        links = notice.links.get(format, {})
        if url := links.get(language):
            return format, url
        if format == "xml" and (url := links.get("mul")):
            return format, url
    return None


def acquire_notice(
    client: TedClient, notice: TedNotice, directory: Path, *, language: str = "deu"
) -> dict:
    record = asdict(notice)
    record.update(
        {
            "title": notice.title,
            "available_formats": notice.available_formats,
            "metadata_retrieved_at": timestamp(),
            "documents": [],
            "download_attempts": [],
            "pdf_validation_status": "not_attempted",
            "xml_validation_status": "not_attempted",
            "failures": [],
            "annotation_status": "not_annotated",
            "manual_review": "pending",
            "shortlist_eligible": False,
        }
    )
    if language not in notice.official_languages:
        record["status"] = (
            "skipped_unknown_language" if not notice.official_languages else "skipped_language"
        )
        logger.warning(
            "%s: skipped; official languages %s do not establish original %s",
            notice.publication_id,
            notice.official_languages,
            language,
        )
        return record
    record["status"] = "acquired"
    for formats in [("xml",), ("pdf", "pdfs")]:
        link = _link(notice, formats, language)
        if link is None:
            message = f"No official {formats[0]} link for original language {language}"
            record["failures"].append(message)
            logger.warning("%s: %s", notice.publication_id, message)
            continue
        format, url = link
        attempt = {
            "format": format,
            "source_url": url,
            "attempted_at": timestamp(),
            "status": "started",
        }
        record["download_attempts"].append(attempt)
        try:
            content = client.download(url, notice.publication_id, format)
            attempt["status"] = "downloaded"
            digest = hashlib.sha256(content).hexdigest()
            extension = "pdf" if format == "pdfs" else format
            relative = Path("documents") / f"{digest}.{extension}"
            target = directory / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            reused = target.exists() and hashlib.sha256(target.read_bytes()).hexdigest() == digest
            if not reused:
                target.write_bytes(content)
            artifact = {
                "format": format,
                "source_url": url,
                "retrieved_at": timestamp(),
                "sha256": digest,
                "bytes": len(content),
                "path": relative.as_posix(),
                "content_reused": reused,
                "language": language if format != "xml" else "mul",
            }
            if extension == "pdf":
                artifact["validation"] = asdict(check_pdf(target))
            else:
                artifact["validation"] = inspect_xml(content)
            record["documents"].append(artifact)
            check = artifact["validation"]
            record[f"{extension}_validation_status"] = check["status"]
            if check["status"] != "passed":
                record["failures"].append(f"{format}: {check.get('reason', 'validation failed')}")
            if format == "xml":
                original = check.get("original_languages", [])
                if original and set(original) != set(notice.official_languages):
                    record["failures"].append("API/XML original-language metadata disagree")
                    record["status"] = "language_conflict"
                    logger.warning("%s: language conflict; skipping PDF", notice.publication_id)
                    break
                if check.get("language_status") == "missing":
                    record["failures"].append(
                        "XML original-language field missing; relying on API metadata"
                    )
        except TedError as exc:
            attempt.update({"status": "failed", "error": str(exc)})
            record["failures"].append(f"{format}: {exc}")
    for failure in record["failures"]:
        logger.warning("%s: %s", notice.publication_id, failure)
    pdf_passed = any(
        doc["format"] in {"pdf", "pdfs"} and doc["validation"]["status"] == "passed"
        for doc in record["documents"]
    )
    record["shortlist_eligible"] = (
        pdf_passed
        and record["status"] != "language_conflict"
        and bool(notice.title)
        and bool(notice.publication_date)
    )
    if not pdf_passed and record["status"] != "language_conflict":
        record["status"] = (
            "unusable_pdf" if record["pdf_validation_status"] == "failed" else "pdf_not_obtained"
        )
    return record


def load_manifest(directory: Path, dataset_version: str) -> dict:
    path = directory / "manifest.json"
    if path.exists():
        manifest = json.loads(path.read_text())
        if manifest["dataset_version"] != dataset_version:
            raise ValueError("Use a separate directory for a different dataset version")
        return manifest
    return {
        "dataset_version": dataset_version,
        "created_at": timestamp(),
        "source": "https://api.ted.europa.eu/v3/notices/search",
        "notices": [],
        "runs": [],
        "shortlist": [],
        "gold_labels": None,
    }


def verified_cache(record: dict, directory: Path) -> bool:
    """Reuse only complete acquisitions whose locally stored bytes still match their hashes."""
    documents = record.get("documents", [])
    if record.get("status") != "acquired" or record.get("failures") or len(documents) != 2:
        return False
    for document in documents:
        path = (directory / document["path"]).resolve()
        if not path.is_relative_to(directory.resolve()) or not path.is_file():
            return False
        if hashlib.sha256(path.read_bytes()).hexdigest() != document["sha256"]:
            return False
    return True


def save_manifest(directory: Path, manifest: dict) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    manifest["updated_at"] = timestamp()
    temporary = directory / "manifest.json.tmp"
    temporary.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")
    temporary.replace(directory / "manifest.json")
