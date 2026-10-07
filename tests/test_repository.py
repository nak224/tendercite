from tendercite.domain.models import ChunkRead, PageRead
from tendercite.repositories.sqlite import SqliteRepository


def test_document_round_trip(tmp_path) -> None:
    repo = SqliteRepository(tmp_path / "test.db")
    page = PageRead(document_id="doc-1", page_number=1, text="Tender text")
    chunk = ChunkRead(
        id="chunk-1",
        document_id="doc-1",
        page_number=1,
        ordinal=0,
        text="Tender text",
        char_start=0,
        char_end=11,
    )

    saved = repo.save_document(
        document_id="doc-1",
        filename="tender.pdf",
        media_type="application/pdf",
        sha256="abc",
        pages=[page],
        chunks=[chunk],
    )

    assert saved.page_count == 1
    assert repo.get_page("doc-1", 1) == page
    assert repo.list_chunks("doc-1") == [chunk]
