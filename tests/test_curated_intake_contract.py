import copy
import json

import pytest
from curated_intake_helpers import intake_body, repository, upload
from pydantic import ValidationError

from tendercite.domain.curated_intake import (
    CuratedTenderIntake,
    intake_analysis_id,
    intake_content_hash,
    intake_finding_id,
)


@pytest.mark.parametrize(
    "field, value",
    [
        ("schema_version", "future"),
        ("source_origin", "llm_generated"),
        ("purpose", " "),
        ("purpose", "x" * 2001),
        ("operator_alias", "a@example.invalid"),
        ("documents", []),
        ("findings", []),
        ("extra", "unexpected"),
    ],
)
def test_invalid_envelope(client, field, value):
    body = intake_body(upload(client))
    body[field] = value
    with pytest.raises(ValidationError):
        CuratedTenderIntake.model_validate_json(json.dumps(body))


@pytest.mark.parametrize(
    "field", ["schema_version", "source_origin", "purpose", "documents", "findings"]
)
def test_required_fields(client, field):
    body = intake_body(upload(client))
    del body[field]
    with pytest.raises(ValidationError):
        CuratedTenderIntake.model_validate_json(json.dumps(body))


@pytest.mark.parametrize(
    "field, value",
    [
        ("item_id", ""),
        ("category", "QUALIFIED"),
        ("requirement_type", "FIT_SCORE"),
        ("evidence", []),
        ("statement", " "),
        ("statement", "x" * 4001),
        ("review_status", "CONFIRMED"),
        ("interpretation_notes", "x" * 1001),
    ],
)
def test_candidate_contract(client, field, value):
    body = intake_body(upload(client))
    body["findings"][0][field] = value
    with pytest.raises(ValidationError):
        CuratedTenderIntake.model_validate_json(json.dumps(body))


@pytest.mark.parametrize(
    "field, value",
    [
        ("page_number", 0),
        ("page_number", True),
        ("page_number", "1"),
        ("quote", " "),
        ("quote", "x" * 4001),
    ],
)
def test_strict_citation_contract(client, field, value):
    body = intake_body(upload(client))
    body["findings"][0]["evidence"][0][field] = value
    with pytest.raises(ValidationError):
        CuratedTenderIntake.model_validate_json(json.dumps(body))


@pytest.mark.parametrize("kind", ["item", "document", "foreign_reference", "unused_document"])
def test_duplicate_or_inconsistent_inventory(client, kind):
    body = intake_body(upload(client))
    if kind == "item":
        body["findings"].append(copy.deepcopy(body["findings"][0]))
    elif kind == "document":
        body["documents"].append(copy.deepcopy(body["documents"][0]))
    elif kind == "foreign_reference":
        body["findings"][0]["evidence"][0]["document_id"] = "unselected"
    else:
        body["documents"].append(dict(document_id="unused", expected_pdf_sha256="0" * 64))
    with pytest.raises(ValidationError):
        CuratedTenderIntake.model_validate_json(json.dumps(body))


def test_valid_contract_identity_and_canonical_order(client):
    document = upload(client)
    body = intake_body(document)
    second = copy.deepcopy(body["findings"][0])
    second["item_id"] = "other"
    body["findings"].append(second)
    first = CuratedTenderIntake.model_validate_json(json.dumps(body))
    body["findings"].reverse()
    reordered = CuratedTenderIntake.model_validate_json(json.dumps(body, sort_keys=True, indent=4))
    assert first == reordered
    assert intake_content_hash(first) == intake_content_hash(reordered)
    assert intake_analysis_id(first) == intake_analysis_id(reordered)
    assert intake_finding_id(first, "other") != intake_finding_id(first, "postgresql")
    assert CuratedTenderIntake.model_validate_json(first.model_dump_json()) == first


def test_total_canonical_content_is_bounded(client):
    body = intake_body(upload(client))
    template = body["findings"][0]
    template["evidence"] *= 20
    for evidence in template["evidence"]:
        evidence["quote"] = "x" * 4000
    body["findings"] = [dict(copy.deepcopy(template), item_id=f"item-{i}") for i in range(100)]
    with pytest.raises(ValidationError, match="content limit"):
        CuratedTenderIntake.model_validate_json(json.dumps(body))


def test_unsupported_evaluation_envelope_is_not_a_production_intake(client):
    body = intake_body(upload(client))
    body["annotation_schema_version"] = "real-tender-annotations-1"
    with pytest.raises(ValidationError):
        CuratedTenderIntake.model_validate_json(json.dumps(body))


def test_intentionally_questionable_interpretation_is_only_unreviewed_candidate(client):
    document = upload(client)
    body = intake_body(document)
    body["findings"][0]["statement"] = (
        "The quote proves all bidder eligibility and ten years of experience."
    )
    request = CuratedTenderIntake.model_validate_json(json.dumps(body))
    repo = repository(client)
    result = repo.commit_curated_intake(request)
    finding = repo.list_findings(result.analysis_id)[0]
    assert finding.review_status == "UNREVIEWED" and finding.reviewed_value is None
    assert finding.original_output is None and finding.confidence is None
    assert repo.list_reviews(finding.id) == []
    assert finding.statement == body["findings"][0]["statement"]
