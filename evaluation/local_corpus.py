"""Local PDF corpus registration, independent of production ingestion and network services."""

import hashlib
import json
import re
import tempfile
from dataclasses import asdict
from datetime import date
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from evaluation.ted_acquisition import check_pdf, timestamp
from tendercite.services.ted import MAX_DOCUMENT_BYTES, publication_id

CORPUS_VERSION = "real-pdf-1"
MANIFEST_VERSION = 1


class SourceMetadata(BaseModel):
    """Verification is a curator declaration, never inferred from a URL or PDF text."""

    model_config = ConfigDict(extra="forbid")
    tender_id: str | None = Field(default=None, min_length=1)
    ted_publication_id: str | None = None
    source_urls: list[str] = Field(default_factory=list)
    retrieval_date: date | None = None
    provenance_notes: str | None = None
    rights_notes: str | None = None
    provenance_status: Literal["unverified", "verified"] = "unverified"
    rights_status: Literal["unverified", "verified"] = "unverified"
    declared_language: str | None = None

    @field_validator("ted_publication_id")
    @classmethod
    def valid_publication_id(cls, value: str | None) -> str | None:
        return publication_id(value) if value is not None else None

    @field_validator("source_urls")
    @classmethod
    def public_source_urls(cls, values: list[str]) -> list[str]:
        for value in values:
            url = urlsplit(value)
            if (
                url.scheme not in {"https", "http"}
                or not url.hostname
                or url.username
                or url.password
            ):
                raise ValueError("source_urls must be HTTP(S) URLs without credentials")
        return list(dict.fromkeys(values))

    @model_validator(mode="after")
    def verification_requires_notes(self):
        if self.provenance_status == "verified" and not (
            self.source_urls and self.retrieval_date and (self.provenance_notes or "").strip()
        ):
            raise ValueError("Verified provenance requires source URLs, retrieval date and notes")
        if self.rights_status == "verified" and not (self.rights_notes or "").strip():
            raise ValueError("Verified rights require rights notes")
        return self

    def flags(self) -> list[str]:
        flags = []
        for name in ("source_urls", "retrieval_date", "provenance_notes", "rights_notes"):
            value = getattr(self, name)
            if not value or isinstance(value, str) and not value.strip():
                flags.append(f"{name}_missing")
        if not self.tender_id:
            flags.append("tender_id_missing")
        if not self.declared_language:
            flags.append("language_undeclared")
        if self.provenance_status != "verified":
            flags.append("provenance_unverified")
        if self.rights_status != "verified":
            flags.append("rights_unverified")
        return flags


class MetadataFile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    defaults: dict = Field(default_factory=dict)
    files: dict[str, dict] = Field(default_factory=dict)


def read_pdf_bytes(path: Path) -> bytes:
    if path.is_symlink():
        raise ValueError("PDF symlinks are not supported; provide the downloaded file itself")
    with path.open("rb") as source:
        content = source.read(MAX_DOCUMENT_BYTES + 1)
    if len(content) > MAX_DOCUMENT_BYTES:
        raise ValueError("PDF exceeds the 25 MiB corpus limit")
    return content


def blob_path(directory: Path, digest: str) -> Path:
    if not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise ValueError("Invalid SHA-256 in corpus manifest")
    path = directory / "pdfs" / f"{digest}.pdf"
    if (
        path.is_symlink()
        or path.parent.is_symlink()
        or not path.resolve().is_relative_to(directory.resolve())
    ):
        raise ValueError("Corpus PDF path must stay inside the corpus directory without symlinks")
    return path


def load_corpus(directory: Path, corpus_version: str) -> dict:
    path = directory / "manifest.json"
    if not path.exists():
        return {
            "manifest_version": MANIFEST_VERSION,
            "corpus_version": corpus_version,
            "created_at": timestamp(),
            "documents": [],
            "tenders": {},
            "import_runs": [],
        }
    corpus = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(corpus, dict):
        raise ValueError("Malformed corpus manifest: expected a JSON object")
    if (
        corpus.get("manifest_version") != MANIFEST_VERSION
        or corpus.get("corpus_version") != corpus_version
    ):
        raise ValueError("Corpus version mismatch; use a separate corpus directory")
    if not isinstance(corpus.get("documents"), list) or not isinstance(
        corpus.get("import_runs"), list
    ):
        raise ValueError("Malformed corpus manifest")
    return corpus


def verify_corpus(directory: Path, corpus: dict) -> list[str]:
    """Verify every registered file, deriving paths from hashes rather than trusting JSON paths."""
    errors = []
    seen = set()
    for document in corpus["documents"]:
        if not isinstance(document, dict):
            errors.append("Malformed document record in corpus manifest")
            continue
        try:
            digest = document["sha256"]
            path = blob_path(directory, digest)
            if document["document_id"] != f"sha256:{digest}" or digest in seen:
                raise ValueError("Inconsistent or duplicate document ID/hash")
            seen.add(digest)
            if document["path"] != f"pdfs/{digest}.pdf":
                raise ValueError("Unexpected corpus PDF path")
            content = read_pdf_bytes(path)
            if len(content) != document["bytes"] or hashlib.sha256(content).hexdigest() != digest:
                raise ValueError("SHA-256/size mismatch; stored PDF was changed")
        except (OSError, ValueError, KeyError, TypeError) as exc:
            errors.append(f"{document.get('document_id', 'unknown document')}: {exc}")
    return errors


def save_corpus(directory: Path, corpus: dict) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=directory, prefix=".manifest-", delete=False
    ) as temporary:
        path = Path(temporary.name)
        temporary.write(json.dumps(corpus, indent=2, ensure_ascii=False) + "\n")
    try:
        path.replace(directory / "manifest.json")
    finally:
        path.unlink(missing_ok=True)


def register_directory(
    input_dir: Path,
    directory: Path,
    *,
    corpus_version: str = CORPUS_VERSION,
    metadata: MetadataFile | None = None,
    defaults: dict | None = None,
) -> dict:
    """Identical PDF bytes share a document while retaining each source association."""
    if not input_dir.is_dir() or input_dir.resolve() == directory.resolve():
        raise ValueError("Provide an input directory separate from the corpus output directory")
    files = sorted(
        (path for path in input_dir.iterdir() if path.suffix.lower() == ".pdf"),
        key=lambda path: path.name,
    )
    if not files:
        raise ValueError("Input directory contains no PDF files (only top-level files are scanned)")
    metadata = metadata or MetadataFile()
    if unknown := set(metadata.files) - {path.name for path in files}:
        raise ValueError(
            f"Metadata references PDF filenames not present in input: {sorted(unknown)}"
        )
    common = metadata.defaults | (defaults or {})
    if unknown := set(common) - set(SourceMetadata.model_fields):
        raise ValueError(f"Unknown default metadata fields: {sorted(unknown)}")
    corpus = load_corpus(directory, corpus_version)
    if errors := verify_corpus(directory, corpus):
        raise ValueError("Existing corpus integrity check failed: " + "; ".join(errors))
    directory.mkdir(parents=True, exist_ok=True)
    documents = {document["sha256"]: document for document in corpus["documents"]}
    run = {"started_at": timestamp(), "results": []}
    for file in files:
        result = {"original_filename": file.name, "status": "rejected", "sha256": None}
        temporary = None
        try:
            source = SourceMetadata.model_validate(common | metadata.files.get(file.name, {}))
            result.update({"source": source.model_dump(mode="json"), "flags": source.flags()})
            content = read_pdf_bytes(file)
            digest = hashlib.sha256(content).hexdigest()
            result.update({"sha256": digest, "document_id": f"sha256:{digest}"})
            document = documents.get(digest)
            if document is None:
                # Parse the exact snapshot whose hash we register, even if the input changes later.
                with tempfile.NamedTemporaryFile(
                    dir=directory, suffix=".pdf", delete=False
                ) as snapshot:
                    temporary = Path(snapshot.name)
                    snapshot.write(content)
                check = check_pdf(temporary)
                result["validation"] = asdict(check)
                if check.status != "passed":
                    raise ValueError(check.reason)
                target = blob_path(directory, digest)
                target.parent.mkdir(parents=True, exist_ok=True)
                if target.exists():
                    if hashlib.sha256(read_pdf_bytes(target)).hexdigest() != digest:
                        raise ValueError(
                            "Existing hash-addressed PDF bytes do not match their filename"
                        )
                else:
                    temporary.replace(target)
                document = {
                    "document_id": f"sha256:{digest}",
                    "sha256": digest,
                    "path": f"pdfs/{digest}.pdf",
                    "bytes": len(content),
                    "page_count": check.pages,
                    "validation": asdict(check),
                    "registered_at": timestamp(),
                    "sources": [],
                }
                documents[digest] = document
                result["status"] = "imported"
            else:
                result["status"] = "deduplicated"
                result["validation"] = document["validation"]
            association = {
                "original_filename": file.name,
                **source.model_dump(mode="json"),
                "flags": source.flags(),
            }
            # Reimports never overwrite prior provenance or duplicate unchanged associations.
            if association not in document["sources"]:
                document["sources"].append(association)
            result["flags"] = association["flags"]
        except ValidationError as exc:
            result["error"] = "Invalid metadata: " + "; ".join(
                f"{'.'.join(map(str, error['loc']))}: {error['msg']}"
                for error in exc.errors(include_input=False, include_url=False)
            )
        except (OSError, ValueError) as exc:
            result["status"] = "rejected"
            result["error"] = str(exc)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        run["results"].append(result)
    corpus["documents"] = sorted(documents.values(), key=lambda document: document["document_id"])
    tenders = {}
    for document in corpus["documents"]:
        for source in document["sources"]:
            if tender_id := source["tender_id"]:
                tenders.setdefault(tender_id, set()).add(document["document_id"])
    corpus["tenders"] = {key: sorted(values) for key, values in sorted(tenders.items())}
    run.update(
        {
            "finished_at": timestamp(),
            "status": "partial"
            if any(r["status"] == "rejected" for r in run["results"])
            else "completed",
        }
    )
    corpus["import_runs"].append(run)
    corpus["updated_at"] = run["finished_at"]
    save_corpus(directory, corpus)
    return run
