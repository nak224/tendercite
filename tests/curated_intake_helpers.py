"""Self-authored PDFs pass through the existing production upload/parser/chunker."""

import json

from evaluation.generate import make_pdf
from tendercite.api.dependencies import get_repository
from tendercite.domain.curated_intake import CuratedTenderIntake
from tendercite.main import app

QUOTE = "The contractor must provide PostgreSQL development experience."


def upload(client, pages=(QUOTE, "Offers must arrive before the stated deadline.")):
    response = client.post(
        "/api/v1/documents",
        files={"file": ("self-authored.pdf", make_pdf(pages), "application/pdf")},
    )
    assert response.status_code == 201
    return response.json()


def intake_body(document, *, quote=QUOTE, **changes):
    return (
        dict(
            schema_version="curated-tender-intake-1",
            source_origin="manual_operator_entry",
            purpose="Self-authored synthetic manual intake",
            operator_alias="synthetic_operator",
            documents=[
                dict(
                    document_id=document["id"],
                    expected_pdf_sha256=document["sha256"],
                    source_note="Generated fixture only",
                )
            ],
            findings=[
                dict(
                    item_id="postgresql",
                    statement=QUOTE,
                    category="MUST",
                    requirement_type="TECHNICAL",
                    evidence=[dict(document_id=document["id"], page_number=1, quote=quote)],
                    interpretation_notes="Synthetic candidate interpretation",
                )
            ],
        )
        | changes
    )


def intake(document, **changes):
    return CuratedTenderIntake.model_validate_json(json.dumps(intake_body(document, **changes)))


def repository(client):
    return app.dependency_overrides[get_repository]()
