"""Offline occurrence and human-review validation for external QA source spans; no scoring."""

import argparse
import logging
from collections import Counter
from pathlib import Path

from pydantic import ValidationError

from evaluation.external_qa_spans import (
    QuestionMetadata,
    ReviewHeader,
    SourceEvidence,
    load_review_context,
    parsed_reference_pages,
    read_local_json,
    write_new_json,
)


def schema_errors(exc):
    return [
        f"{'.'.join(map(str, e['loc'])) or 'record'}: {e['msg']}"
        for e in exc.errors(include_input=False, include_url=False)
    ]


def source_identity(entry):
    if not isinstance(entry, dict):
        return None
    values = tuple(entry.get(k) for k in ("source_document", "pdf_sha256", "exact_source_quote"))
    page = entry.get("page_number")
    if not all(isinstance(v, str) for v in values) or type(page) is not int:
        return None
    return (*values, page)


def validate_review(payload, context) -> dict:
    if not isinstance(payload, dict) or not isinstance(payload.get("questions"), list):
        raise ValueError("Review must be a JSON object with a questions array")
    rows = payload["questions"]
    if len(rows) > 1000:
        raise ValueError("Review exceeds 1000 questions")
    global_errors = []
    try:
        header = ReviewHeader.model_validate({k: v for k, v in payload.items() if k != "questions"})
        if header.model_dump() != context.header:
            global_errors.append("Review header does not match pinned source/provenance hashes")
    except ValidationError as exc:
        global_errors.extend(schema_errors(exc))
    counts = Counter(
        row.get("question_id")
        for row in rows
        if isinstance(row, dict) and isinstance(row.get("question_id"), str)
    )
    missing = sorted(set(context.questions) - set(counts))
    if missing:
        global_errors.append("Missing eligible question IDs: " + ", ".join(missing))
    # Recheck and parse immutable byte snapshots; page exports are never trusted as input gold.
    pages = parsed_reference_pages(context)
    output = []
    for index, row in enumerate(rows):
        question_id = row.get("question_id") if isinstance(row, dict) else None
        errors, evidence_results, verified = [], [], []
        expected = context.questions.get(question_id) if isinstance(question_id, str) else None
        if expected is None:
            errors.append("Question ID is not an eligible case in the selected family")
        if isinstance(question_id, str) and counts[question_id] > 1:
            errors.append("Duplicate question ID; all copies excluded")
        try:
            metadata = QuestionMetadata.model_validate(
                {k: v for k, v in row.items() if k != "evidence"} if isinstance(row, dict) else row
            )
            if expected and metadata.model_dump(
                exclude={"annotation_notes"}
            ) != expected.model_dump(exclude={"annotation_notes"}):
                errors.append("Original question/type/document references or hashes were changed")
        except ValidationError as exc:
            errors.extend(schema_errors(exc))
        evidence = row.get("evidence") if isinstance(row, dict) else None
        if not isinstance(evidence, list) or len(evidence) > 100:
            errors.append("evidence must be an array of at most 100 records")
            evidence = []
        identities = [source_identity(entry) for entry in evidence]
        duplicates = Counter(identity for identity in identities if identity is not None)
        required = (
            {ref.filename: ref.pdf_sha256 for ref in expected.source_documents} if expected else {}
        )
        for ordinal, (entry, identity) in enumerate(zip(evidence, identities, strict=True)):
            failures, model = [], None
            if identity is not None and duplicates[identity] > 1:
                failures.append(
                    "Duplicate evidence location/quote within question; all copies excluded"
                )
            try:
                model = SourceEvidence.model_validate(entry)
                if model.source_document not in required:
                    failures.append(
                        "Evidence document is not an original reference for this question/family"
                    )
                elif model.pdf_sha256 != required[model.source_document]:
                    failures.append("Evidence PDF hash does not match the acquisition manifest")
                else:
                    texts = pages[model.source_document]
                    if model.page_number not in texts:
                        failures.append("Page number does not exist in the parsed PDF")
                    elif model.exact_source_quote not in texts[model.page_number]:
                        failures.append("Exact quote does not occur verbatim on the parsed page")
            except ValidationError as exc:
                failures.extend(schema_errors(exc))
            ready = bool(
                model
                and not failures
                and model.review_status == "VERIFIED"
                and not errors
                and not global_errors
            )
            exclusions = failures.copy()
            if model and model.review_status != "VERIFIED":
                exclusions.append(
                    f"Review status {model.review_status}; independent VERIFIED review required"
                )
            if errors or global_errors:
                exclusions.append("Question or source provenance is invalid")
            evidence_results.append(
                {
                    "input_index": ordinal,
                    "source_document": entry.get("source_document")
                    if isinstance(entry, dict)
                    else None,
                    "review_status": entry.get("review_status")
                    if isinstance(entry, dict)
                    else None,
                    "review_notes": model.review_notes if model else None,
                    "validation_errors": failures,
                    "exclusion_reasons": exclusions,
                    "independently_verified": ready,
                }
            )
            if ready:
                verified.append(model.model_dump())
        invalid = bool(
            errors or global_errors or any(r["validation_errors"] for r in evidence_results)
        )
        coverage = [
            {
                "filename": name,
                "pdf_sha256": digest,
                "verified_span_count": sum(e["source_document"] == name for e in verified),
            }
            for name, digest in required.items()
        ]
        complete = bool(
            coverage and not invalid and all(d["verified_span_count"] for d in coverage)
        )
        output.append(
            {
                "input_index": index,
                "question_id": question_id,
                "annotation_notes": row.get("annotation_notes", "")
                if isinstance(row, dict)
                else None,
                "validation_errors": errors,
                "evidence_results": evidence_results,
                "required_document_review": coverage,
                "evidence_complete": complete,
                "verified_source_spans": verified if not invalid else [],
            }
        )
    invalid_records = sum(
        bool(q["validation_errors"]) or any(r["validation_errors"] for r in q["evidence_results"])
        for q in output
    )
    complete_count = sum(q["evidence_complete"] for q in output)
    return {
        **context.header,
        "report_kind": "external-qa-source-span-validation-1",
        "status": "INVALID"
        if global_errors or invalid_records
        else ("COMPLETE" if complete_count == len(context.questions) else "INCOMPLETE"),
        "eligible_questions": len(context.questions),
        "evidence_complete_questions": complete_count,
        "global_errors": global_errors,
        "missing_question_ids": missing,
        "acquisition_exclusions": context.exclusions,
        "questions": output,
        "semantic_support": (
            "Human reviewer declaration; quote occurrence does not prove interpretation"
        ),
        "retrieval_scoring_performed": False,
    }


def main(argv=None):
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("annotations", type=Path)
    cli.add_argument("--acquisition-dir", type=Path, required=True)
    cli.add_argument("--retrieval-results", type=Path, required=True)
    cli.add_argument("--report", type=Path, required=True, help="Fresh ignored JSON file")
    args = cli.parse_args(argv)
    try:
        payload, annotation_hash = read_local_json(args.annotations)
        context = load_review_context(args.acquisition_dir, args.retrieval_results)
        report = validate_review(payload, context)
        report["annotations_sha256"] = annotation_hash
        write_new_json(args.report, report)
        print(
            f"{report['status']}: {report['evidence_complete_questions']}/"
            f"{report['eligible_questions']} questions evidence-complete; no retrieval scores"
        )
        return 0 if report["status"] == "COMPLETE" else 1
    except (OSError, ValueError, KeyError, TypeError) as exc:
        logging.error("Source-span validation failed: %s", exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
