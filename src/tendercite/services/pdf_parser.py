from dataclasses import dataclass
from pathlib import Path

from pypdf import PdfReader

from tendercite.services.text import normalize_page_text


@dataclass(frozen=True, slots=True)
class ParsedPage:
    page_number: int
    text: str


class PdfParseError(RuntimeError):
    pass


class PyPdfParser:
    """Lightweight parser for digitally generated PDFs.

    OCR and advanced layout reconstruction are intentionally outside the v0.1 path.
    The parser interface can later be backed by Docling without changing API models.
    """

    def parse(self, path: Path) -> list[ParsedPage]:
        try:
            reader = PdfReader(str(path))
        except Exception as exc:  # pypdf exposes multiple parse exception types
            raise PdfParseError(f"Could not open PDF: {exc}") from exc

        pages: list[ParsedPage] = []
        for index, page in enumerate(reader.pages, start=1):
            try:
                text = page.extract_text() or ""
            except Exception as exc:
                raise PdfParseError(f"Could not extract page {index}: {exc}") from exc
            pages.append(ParsedPage(page_number=index, text=normalize_page_text(text)))
        return pages
