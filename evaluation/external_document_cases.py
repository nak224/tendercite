"""Pinned external QA eligibility; upstream references remain candidate labels."""

import json
from dataclasses import dataclass
from pathlib import Path

from evaluation import external_benchmark as acquisition
from evaluation.external_qa import QARecord, validate_metadata

DATASET_REVISION = "f2b0856ef0a5c327afb4f40c7cde5dd44c8d56ea"
FAMILY = "BSGEE_2025-002_School_Information_Systems"


@dataclass
class PreparedCases:
    eligible: list[QARecord]
    excluded: list[dict]
    documents: dict[str, dict]
    manifest: dict
    validation: dict


def prepare_cases(
    directory: Path, *, family: str = FAMILY, revision: str = DATASET_REVISION
) -> PreparedCases:
    """Require the original manifest/report, then recheck bytes and parsing offline."""
    directory = acquisition.confined(directory)
    manifest = json.loads(acquisition.confined(directory, "manifest.json").read_text("utf-8"))
    previous = json.loads(acquisition.confined(directory, "validation.json").read_text("utf-8"))
    if (
        manifest.get("revision") != revision
        or manifest.get("family") != family
        or previous.get("revision") != revision
        or previous.get("selected_family") != family
        or previous.get("source_repository") != acquisition.REPOSITORY
    ):
        raise ValueError("Acquisition manifest/report does not match the pinned revision/family")
    # A partial PDF failure is allowed, but missing/corrupt metadata is not.
    validation = acquisition.validate_local(directory)
    inventory = {spec["filename"]: spec for spec in manifest["inventory"]}
    files = {file["filename"]: file for file in manifest["files"]}
    qa = files[acquisition.QA_FILENAME]
    content = acquisition.read_artifact(
        acquisition.confined(directory, qa["path"]), inventory[qa["filename"]], qa
    )
    records, summary = validate_metadata(json.loads(content), set(inventory))
    if summary["invalid_records"] or not records:
        raise ValueError("Invalid/empty metadata; labels cannot be silently repaired or dropped")
    selected = [record for record in records if record.family == family]
    if not selected:
        raise ValueError("Selected family has no cases")
    pdfs = validation["family_validation"]["pdfs"]
    documents = {}
    for filename in sorted(inventory):
        if not filename.startswith(f"data/{family}/") or not filename.lower().endswith(".pdf"):
            continue
        if pdfs.get(filename, {}).get("status") == "passed":
            record = files[filename]
            documents[Path(filename).name] = {
                **record,
                "local_path": acquisition.confined(directory, record["path"]),
            }
    eligible, excluded = [], []
    for record in selected:
        reasons = []
        if record.question_type == "Unanswerable":
            reasons.append(
                "Unanswerable candidate: positive document-reference metrics not defined"
            )
        if not record.source_documents:
            reasons.append("No source-document references")
        for name in record.source_documents:
            if name not in documents:
                check = pdfs.get(f"data/{family}/{name}", {})
                reasons.append(
                    f"{name}: {check.get('reason', check.get('status', 'not acquired'))}"
                )
        if reasons:
            excluded.append(
                {
                    "id": record.id,
                    "question_type": record.question_type,
                    "source_documents": record.source_documents,
                    "reasons": reasons,
                }
            )
        else:
            eligible.append(record)
    return PreparedCases(eligible, excluded, documents, manifest, validation)
