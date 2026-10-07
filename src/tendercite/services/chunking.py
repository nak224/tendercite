from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class TextChunk:
    page_number: int
    ordinal: int
    text: str
    char_start: int
    char_end: int


def chunk_page(
    page_number: int,
    text: str,
    *,
    chunk_size: int = 1200,
    overlap: int = 150,
) -> list[TextChunk]:
    """Split one page into overlapping chunks without crossing page boundaries."""
    if chunk_size <= 0:
        raise ValueError("chunk_size must be positive")
    if overlap < 0 or overlap >= chunk_size:
        raise ValueError("overlap must satisfy 0 <= overlap < chunk_size")
    if not text:
        return []

    chunks: list[TextChunk] = []
    start = 0
    ordinal = 0
    text_length = len(text)

    while start < text_length:
        hard_end = min(start + chunk_size, text_length)
        end = hard_end

        if hard_end < text_length:
            search_floor = min(start + max(chunk_size // 2, 1), hard_end)
            candidates = [
                text.rfind("\n", search_floor, hard_end),
                text.rfind(". ", search_floor, hard_end),
                text.rfind(" ", search_floor, hard_end),
            ]
            best = max(candidates)
            if best > start:
                end = best + (
                    1 if text[best] == "\n" else 2 if text[best : best + 2] == ". " else 1
                )

        chunk_text = text[start:end].strip()
        if chunk_text:
            chunks.append(
                TextChunk(
                    page_number=page_number,
                    ordinal=ordinal,
                    text=chunk_text,
                    char_start=start + len(text[start:end]) - len(text[start:end].lstrip()),
                    char_end=end - (len(text[start:end]) - len(text[start:end].rstrip())),
                )
            )
            ordinal += 1

        if end >= text_length:
            break
        start = max(end - overlap, start + 1)

    return chunks


def chunk_pages(pages: list[tuple[int, str]]) -> list[TextChunk]:
    chunks: list[TextChunk] = []
    for page_number, text in pages:
        chunks.extend(chunk_page(page_number, text))
    return chunks
