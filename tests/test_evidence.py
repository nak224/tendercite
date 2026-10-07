from tendercite.domain.models import GroundingStatus
from tendercite.services.evidence import validate_evidence


def test_valid_quote_is_grounded_to_page() -> None:
    evidence = validate_evidence(
        document_id="doc-1",
        page_number=3,
        page_text="Mindestens zwei vergleichbare Referenzprojekte sind nachzuweisen.",
        quote="zwei vergleichbare Referenzprojekte",
    )

    assert evidence.grounding_status is GroundingStatus.VERIFIED_QUOTE
    assert evidence.char_start is not None
    assert evidence.char_end is not None
    assert evidence.text_sha256 is not None


def test_unseen_quote_is_rejected() -> None:
    evidence = validate_evidence(
        document_id="doc-1",
        page_number=3,
        page_text="Eine Betriebshaftpflicht ist nachzuweisen.",
        quote="ISO 27001 ist zwingend erforderlich.",
    )

    assert evidence.grounding_status is GroundingStatus.INVALID_QUOTE
    assert evidence.char_start is None
