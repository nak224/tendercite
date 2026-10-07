import pytest

from tendercite.services.chunking import chunk_page


def test_chunks_never_cross_page_boundary() -> None:
    text = "A sentence. " * 300
    chunks = chunk_page(7, text, chunk_size=200, overlap=25)

    assert len(chunks) > 1
    assert all(chunk.page_number == 7 for chunk in chunks)
    assert all(0 <= chunk.char_start < chunk.char_end <= len(text) for chunk in chunks)


def test_chunking_rejects_invalid_overlap() -> None:
    with pytest.raises(ValueError):
        chunk_page(1, "text", chunk_size=100, overlap=100)
