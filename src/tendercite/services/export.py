import csv
import html
import io
import json
import re

DISCLAIMER = (
    "AI-assisted findings require human review. Verified quotes do not prove "
    "statement correctness. This is not legal or procurement advice."
)
COLUMNS = [
    "finding_id",
    "analysis_run_id",
    "original_statement",
    "effective_statement",
    "category",
    "requirement_type",
    "review_status",
    "grounding_status",
    "assessment_status",
    "rationale",
    "document_id",
    "document_name",
    "page_number",
    "quote",
    "evidence_grounding_status",
]


def export_records(repository, analysis_run_id=None):
    records = []
    for finding in repository.list_findings(analysis_run_id):
        matrix = repository.matrix_row(finding)
        record = finding.model_dump(mode="json")
        record["assessment"] = matrix.model_dump(mode="json") if matrix else None
        record["review_history"] = [
            e.model_dump(mode="json") for e in repository.list_reviews(finding.id)
        ]
        for evidence in record["evidence"]:
            document = repository.get_document(evidence["document_id"])
            evidence["document_name"] = document.filename if document else None
        records.append(record)
    return records


def flatten(records):
    for record in records:
        for evidence in record["evidence"] or [{}]:
            assessment = record["assessment"] or {}
            value = record["effective_value"]
            yield dict(
                zip(
                    COLUMNS,
                    [
                        record["id"],
                        record["analysis_run_id"],
                        record["statement"],
                        value["statement"],
                        value["category"],
                        value["requirement_type"],
                        record["review_status"],
                        record["grounding_status"],
                        assessment.get("status", ""),
                        assessment.get("rationale", ""),
                        evidence.get("document_id", ""),
                        evidence.get("document_name", ""),
                        evidence.get("page_number", ""),
                        evidence.get("quote", ""),
                        evidence.get("grounding_status", "MISSING_EVIDENCE"),
                    ],
                    strict=True,
                )
            )


def csv_cell(value):
    text = "" if value is None else str(value)
    if text.lstrip().startswith(("=", "+", "-", "@")) or text.startswith(("\t", "\r", "\n")):
        return "'" + text
    return text


def markdown_cell(value):
    text = html.escape("" if value is None else str(value))
    text = re.sub(r"([\\`*_{}\[\]()#+.!|>-])", r"\\\1", text)
    return text.replace("\r", "").replace("\n", "<br>")


def render_export(records, format):
    if format == "json":
        return json.dumps({"notice": DISCLAIMER, "findings": records}, ensure_ascii=False, indent=2)
    rows = list(flatten(records))
    if format == "csv":
        output = io.StringIO(newline="")
        writer = csv.DictWriter(output, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows({key: csv_cell(value) for key, value in row.items()} for row in rows)
        return output.getvalue()
    output = [
        "# TenderCite analysis",
        "",
        DISCLAIMER,
        "",
        "| " + " | ".join(COLUMNS) + " |",
        "| " + " | ".join("---" for _ in COLUMNS) + " |",
    ]
    output.extend(
        "| " + " | ".join(markdown_cell(row[col]) for col in COLUMNS) + " |" for row in rows
    )
    return "\n".join(output) + "\n"
