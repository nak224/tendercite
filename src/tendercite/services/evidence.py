import hashlib

from tendercite.domain.models import EvidenceRef, GroundingStatus
from tendercite.services.text import normalize_quote


def validate_evidence(*, document_id: str, page_number: int, page_text: str, quote: str) -> EvidenceRef:
    """Validate that a cited quote occurs on the declared page.

    Validation uses whitespace-normalized text to absorb common PDF extraction spacing
    artifacts. The returned offsets refer to that normalized single-line representation.
    """
    normalized_page = normalize_quote(page_text)
    normalized_quote = normalize_quote(quote)
    if not normalized_quote:
        return EvidenceRef(
            document_id=document_id,
            page_number=page_number,
            quote=quote,
            grounding_status=GroundingStatus.MISSING_EVIDENCE,
        )

    start = normalized_page.find(normalized_quote)
    if start < 0:
        return EvidenceRef(
            document_id=document_id,
            page_number=page_number,
            quote=quote,
            text_sha256=hashlib.sha256(normalized_page.encode("utf-8")).hexdigest(),
            grounding_status=GroundingStatus.INVALID_QUOTE,
        )

    end = start + len(normalized_quote)
    return EvidenceRef(
        document_id=document_id,
        page_number=page_number,
        quote=normalized_quote,
        char_start=start,
        char_end=end,
        text_sha256=hashlib.sha256(normalized_page.encode("utf-8")).hexdigest(),
        grounding_status=GroundingStatus.VERIFIED_QUOTE,
    )
