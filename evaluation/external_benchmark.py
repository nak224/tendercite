"""Bounded Hugging Face acquisition, immutable local bytes and document-only QA validation."""

import hashlib
import json
import os
import re
import tempfile
from dataclasses import asdict
from pathlib import Path
from urllib.parse import quote, urlsplit

from evaluation.external_qa import family_validation, safe_name, safe_repo_path, validate_metadata
from evaluation.ted_acquisition import check_pdf, timestamp
from evaluation.validate_annotations import write_json

REPOSITORY = "tmskss/eu-tenders-with-questions-for-agentic-checklist-filling"
QA_FILENAME = "eu-tenders-with-questions-for-agentic-checklist-filling.json"
EXTERNAL_ROOT = Path(__file__).resolve().parents[1] / "data/evaluation/external"
PILOT_VERSION = "external-eu-qa-1"
METADATA_LIMIT = 2 * 1024 * 1024
PDF_LIMIT = 25 * 1024 * 1024


def confined(directory: Path, relative: str = "") -> Path:
    """Only the ignored external root; reject symlinked directories/files and traversal."""
    root = EXTERNAL_ROOT.absolute()
    target = directory.absolute() / safe_repo_path(relative) if relative else directory.absolute()
    if root.resolve() != root or not target.resolve().is_relative_to(root):
        raise ValueError(
            "Artifacts must stay under ignored data/evaluation/external without symlinks"
        )
    for path in [target, *target.parents]:
        if path.is_symlink():
            raise ValueError("Symlinks are not supported in external artifact paths")
        if path == root:
            break
    return target


class HuggingFaceSource:
    """Public official SDK, inherited proxy/CA trust, no endpoint or TLS overrides."""

    def __init__(self, directory: Path):
        self.directory = confined(directory)
        os.environ["HF_XET_CACHE"] = str(self.directory / ".xet-cache")

    def describe(self, revision: str) -> dict:
        from huggingface_hub import HfApi, constants

        confined(Path(constants.HF_XET_CACHE))
        info = HfApi(token=False).dataset_info(
            REPOSITORY, revision=revision, files_metadata=True, timeout=20
        )
        card = info.card_data.to_dict() if info.card_data else {}
        return {
            "revision": info.sha,
            "card": {key: card.get(key) for key in ("license", "language", "task_categories")},
            "files": [
                {
                    "filename": item.rfilename,
                    "size": item.size,
                    "upstream_sha256": item.lfs.sha256 if item.lfs else None,
                    "git_blob_sha1": item.blob_id if not item.lfs else None,
                }
                for item in info.siblings
            ],
        }

    def download(self, filename: str, revision: str, staging: Path) -> Path:
        from huggingface_hub import hf_hub_download

        return Path(
            hf_hub_download(
                repo_id=REPOSITORY,
                repo_type="dataset",
                filename=filename,
                revision=revision,
                local_dir=staging,
                cache_dir=self.directory / ".hub-cache",
                token=False,
                etag_timeout=20,
            )
        )


def failure_details(exc: Exception) -> dict:
    """Keep actual host/status and proxy-denial evidence, strip signed URL queries."""
    message = str(exc)
    urls = re.findall(r"https?://[^\s\"<>]+", message)
    response = getattr(exc, "response", None)
    request = getattr(exc, "request", None)
    hostname = getattr(getattr(request, "url", None), "host", None)
    if hostname is None and urls:
        hostname = urlsplit(urls[-1].rstrip(").,:")).hostname
    body = getattr(response, "text", "")
    proxy_denied = any(
        marker in (message + body).lower()
        for marker in ("blocked by proxy", "proxy denied", "domain_not_allowed", "policy_denied")
    )
    message = re.sub(r"https?://[^\s\"<>]+", lambda m: urlsplit(m[0]).hostname or "[URL]", message)
    return {
        "error_type": type(exc).__name__,
        "error": message[:600],
        "hostname": hostname,
        "http_status": getattr(response, "status_code", None),
        "proxy_denial_observed": proxy_denied,
    }


def read_artifact(path: Path, spec: dict, previous: dict | None = None) -> bytes:
    limit = PDF_LIMIT if spec["filename"].lower().endswith(".pdf") else METADATA_LIMIT
    with path.open("rb") as stream:
        content = stream.read(limit + 1)
    if len(content) > limit or len(content) != spec["size"]:
        raise ValueError("Artifact size does not match bounded upstream inventory")
    digest = hashlib.sha256(content).hexdigest()
    expected = spec.get("upstream_sha256")
    if expected and digest != expected or previous and digest != previous.get("sha256"):
        raise ValueError("SHA-256 mismatch; changed bytes are never overwritten")
    if blob := spec.get("git_blob_sha1"):
        git_digest = hashlib.sha1(
            b"blob " + str(len(content)).encode() + b"\0" + content
        ).hexdigest()
        if git_digest != blob:
            raise ValueError("Git blob checksum mismatch")
    return content


def acquire_file(directory: Path, spec: dict, revision: str, source, previous: dict | None) -> dict:
    filename = safe_repo_path(spec["filename"])
    target = confined(directory, f"artifacts/{filename}")
    url = (
        f"https://huggingface.co/datasets/{REPOSITORY}/resolve/{revision}/"
        f"{quote(filename, safe='/')}"
    )
    result = {
        **(previous or {}),
        "filename": filename,
        "path": f"artifacts/{filename}",
        "url": url,
        "source_repository": REPOSITORY,
        "revision": revision,
        "status": "failed",
    }
    limit = PDF_LIMIT if filename.lower().endswith(".pdf") else METADATA_LIMIT
    try:
        if type(spec.get("size")) is not int or not 0 < spec["size"] <= limit:
            raise ValueError("Missing, empty or oversized upstream artifact size")
        if target.exists():
            if not previous and not (spec.get("upstream_sha256") or spec.get("git_blob_sha1")):
                raise ValueError("Existing artifact has no verified checksum baseline")
            content = read_artifact(target, spec, previous)
        else:
            if previous and previous.get("sha256"):
                raise ValueError("Previously acquired file is missing; no silent replacement")
            with tempfile.TemporaryDirectory(dir=directory, prefix=".download-") as temporary:
                staging = Path(temporary)
                downloaded = source.download(filename, revision, staging)
                if not downloaded.resolve().is_relative_to(staging.resolve()):
                    raise ValueError("SDK download path escaped staging directory")
                content = read_artifact(downloaded, spec)
                target.parent.mkdir(parents=True, exist_ok=True)
                with target.open("xb") as output:
                    output.write(content)  # Exclusive creation; never overwrite an existing file.
        result.update(
            {
                "status": "downloaded",
                "sha256": hashlib.sha256(content).hexdigest(),
                "bytes": len(content),
                "retrieved_at": (previous or {}).get("retrieved_at") or timestamp(),
            }
        )
    except Exception as exc:
        result.update(failure_details(exc))
    return result


def acquire(
    revision: str,
    directory: Path,
    *,
    family: str | None = None,
    download_pdfs: bool = False,
    source=None,
) -> dict:
    directory = confined(directory)
    directory.mkdir(parents=True, exist_ok=True)
    if not revision.strip() or len(revision) > 100:
        raise ValueError("Provide a dataset revision or immutable commit SHA")
    if family:
        safe_name(family)
    if download_pdfs and not family:
        raise ValueError("PDF downloads require exactly one --family")
    previous_path = confined(directory, "manifest.json")
    previous = (
        json.loads(previous_path.read_text(encoding="utf-8")) if previous_path.exists() else {}
    )
    if not isinstance(previous, dict):
        raise ValueError("Malformed acquisition manifest")
    source = source or HuggingFaceSource(directory)
    try:
        upstream = source.describe(revision)
    except Exception as exc:
        report = {
            "pilot_version": PILOT_VERSION,
            "source_repository": REPOSITORY,
            "requested_revision": revision,
            "status": "acquisition_failed",
            "failures": [failure_details(exc)],
            "attempted_at": timestamp(),
        }
        write_json(confined(directory, "acquisition-failure.json"), report)
        return report
    resolved = upstream["revision"]
    if not isinstance(resolved, str) or not re.fullmatch(r"[0-9a-f]{40}", resolved):
        raise ValueError("Hugging Face did not resolve an immutable Git commit SHA")
    if previous and (
        previous.get("source_repository") != REPOSITORY
        or previous.get("revision") != resolved
        or previous.get("family") not in {None, family}
    ):
        raise ValueError(
            "Existing acquisition uses another revision/family; use a new output directory"
        )
    specs = upstream["files"]
    if (
        not isinstance(specs, list)
        or len(specs) > 256
        or len({spec["filename"] for spec in specs}) != len(specs)
    ):
        raise ValueError("Repository inventory is oversized or contains duplicate paths")
    inventory = {safe_repo_path(spec["filename"]): spec for spec in specs}
    selected = [
        inventory.get(name, {"filename": name, "size": None}) for name in ("README.md", QA_FILENAME)
    ]
    if download_pdfs:
        pdfs = [
            spec
            for name, spec in inventory.items()
            if name.startswith(f"data/{family}/") and name.lower().endswith(".pdf")
        ]
        if len(pdfs) > 32 or sum(spec.get("size") or PDF_LIMIT for spec in pdfs) > 64 * 1024 * 1024:
            raise ValueError("Selected family exceeds 32 PDFs / 64 MiB bound")
        selected.extend(sorted(pdfs, key=lambda spec: spec["filename"]))
    old_files = {item["filename"]: item for item in previous.get("files", [])}
    files = [
        acquire_file(directory, spec, resolved, source, old_files.get(spec["filename"]))
        for spec in selected
    ]
    # A metadata-only recheck must retain previously acquired PDF provenance.
    selected_names = {spec["filename"] for spec in selected}
    files.extend(item for name, item in old_files.items() if name not in selected_names)
    manifest = {
        "pilot_version": PILOT_VERSION,
        "source_repository": REPOSITORY,
        "requested_revision": revision,
        "revision": resolved,
        "family": family,
        "download_pdfs": download_pdfs,
        "card_metadata": upstream["card"],
        "inventory": list(inventory.values()),
        "files": files,
        "dataset_rights": "upstream declaration only; document rights unverified",
        "label_origin": "upstream_candidates_not_independent_human_gold",
    }
    write_json(previous_path, manifest)
    return validate_local(directory)


def validate_local(directory: Path) -> dict:
    directory = confined(directory)
    manifest = json.loads(confined(directory, "manifest.json").read_text(encoding="utf-8"))
    if not isinstance(manifest, dict) or (
        manifest.get("source_repository") != REPOSITORY
        or manifest.get("pilot_version") != PILOT_VERSION
    ):
        raise ValueError("Unsupported acquisition manifest")
    if not isinstance(manifest.get("files"), list) or not isinstance(
        manifest.get("inventory"), list
    ):
        raise ValueError("Malformed acquisition manifest files/inventory")
    inventory = {safe_repo_path(spec["filename"]): spec for spec in manifest["inventory"]}
    pdfs, issues, metadata = {}, [], None
    for record in manifest["files"]:
        filename = safe_repo_path(record["filename"])
        if record.get("path") != f"artifacts/{filename}":
            raise ValueError("Artifact path contradicts its repository filename")
        if record["status"] != "downloaded":
            issues.append(
                {
                    "filename": filename,
                    **{
                        key: record.get(key)
                        for key in ("error", "hostname", "http_status", "proxy_denial_observed")
                    },
                }
            )
            if filename.lower().endswith(".pdf"):
                pdfs[filename] = {"status": "not_acquired"}
            continue
        try:
            content = read_artifact(
                confined(directory, record["path"]), inventory[filename], record
            )
            if filename == QA_FILENAME:
                metadata = json.loads(content)
            elif filename.lower().endswith(".pdf"):
                with tempfile.TemporaryDirectory(dir=directory, prefix=".parse-") as temporary:
                    snapshot = Path(temporary) / "snapshot.pdf"
                    snapshot.write_bytes(content)
                    check = asdict(check_pdf(snapshot))
                pdfs[filename] = check
                if check["status"] != "passed":
                    issues.append({"filename": filename, "error": check["reason"]})
        except (OSError, ValueError, KeyError, TypeError) as exc:
            issues.append({"filename": filename, "error": str(exc)})
            if filename.lower().endswith(".pdf"):
                pdfs[filename] = {"status": "integrity_failed"}
    records, summary = [], {"records": None, "valid_records": 0}
    if metadata is None:
        issues.append({"error": "QA metadata was not acquired or could not be validated"})
    else:
        try:
            records, summary = validate_metadata(metadata, set(inventory))
            if summary["invalid_records"] or not summary["records"]:
                issues.append({"error": "Invalid or empty QA metadata; inspect metadata summary"})
        except ValueError as exc:
            issues.append({"error": str(exc)})
    family = manifest["family"]
    if family and family not in {record.family for record in records}:
        issues.append({"error": "Selected family has no supported QA records"})
    result = {
        "pilot_version": PILOT_VERSION,
        "source_repository": REPOSITORY,
        "revision": manifest["revision"],
        "selected_family": family,
        "status": "failed" if issues else "validated",
        "pdf_downloads_requested": manifest["download_pdfs"],
        "validated_at": timestamp(),
        "acquisition_files": manifest["files"],
        "card_metadata": manifest["card_metadata"],
        "dataset_rights": manifest["dataset_rights"],
        "label_origin": manifest["label_origin"],
        "metadata": summary,
        "family_validation": family_validation(records, family, pdfs),
        "issues": issues,
        "retrieval_scores": None,
        "llm_evaluation_performed": False,
    }
    write_json(confined(directory, "validation.json"), result)
    return result
