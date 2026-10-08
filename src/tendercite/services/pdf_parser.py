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
    """Text PDFs only; limits reduce accidental resource use but are not a parser sandbox."""

    def parse(
        self, path: Path, *, max_pages: int = 500, max_chars: int = 2_000_000
    ) -> list[ParsedPage]:
        try:
            with path.open("rb") as source:
                reader = PdfReader(source)
                if reader.is_encrypted:
                    raise PdfParseError("Encrypted PDFs are not supported")
                if not 1 <= len(reader.pages) <= max_pages:
                    raise PdfParseError("PDF page count exceeds supported limits")
                pages = []
                total = 0
                for index, page in enumerate(reader.pages, start=1):
                    text = normalize_page_text(page.extract_text() or "")
                    total += len(text)
                    if total > max_chars:
                        raise PdfParseError("Extracted text exceeds configured limit")
                    pages.append(ParsedPage(page_number=index, text=text))
                return pages
        except PdfParseError:
            raise
        except Exception as exc:
            raise PdfParseError("Could not parse PDF; malformed or unsupported document") from exc
