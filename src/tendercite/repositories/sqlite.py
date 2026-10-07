import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from tendercite.domain.models import ChunkRead, DocumentRead, PageRead


class SqliteRepository:
    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def _initialize(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS documents (
                    id TEXT PRIMARY KEY,
                    filename TEXT NOT NULL,
                    media_type TEXT NOT NULL,
                    sha256 TEXT NOT NULL,
                    page_count INTEGER NOT NULL,
                    chunk_count INTEGER NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS pages (
                    document_id TEXT NOT NULL,
                    page_number INTEGER NOT NULL,
                    text TEXT NOT NULL,
                    PRIMARY KEY (document_id, page_number),
                    FOREIGN KEY (document_id) REFERENCES documents(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS chunks (
                    id TEXT PRIMARY KEY,
                    document_id TEXT NOT NULL,
                    page_number INTEGER NOT NULL,
                    ordinal INTEGER NOT NULL,
                    text TEXT NOT NULL,
                    char_start INTEGER NOT NULL,
                    char_end INTEGER NOT NULL,
                    FOREIGN KEY (document_id) REFERENCES documents(id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_chunks_document ON chunks(document_id);
                CREATE INDEX IF NOT EXISTS idx_chunks_page ON chunks(document_id, page_number);
                """
            )

    def save_document(
        self,
        *,
        document_id: str,
        filename: str,
        media_type: str,
        sha256: str,
        pages: list[PageRead],
        chunks: list[ChunkRead],
    ) -> DocumentRead:
        created_at = datetime.now(UTC)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO documents(
                    id, filename, media_type, sha256, page_count, chunk_count, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    document_id,
                    filename,
                    media_type,
                    sha256,
                    len(pages),
                    len(chunks),
                    created_at.isoformat(),
                ),
            )
            conn.executemany(
                "INSERT INTO pages(document_id, page_number, text) VALUES (?, ?, ?)",
                [(p.document_id, p.page_number, p.text) for p in pages],
            )
            conn.executemany(
                """
                INSERT INTO chunks(
                    id, document_id, page_number, ordinal, text, char_start, char_end
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        c.id,
                        c.document_id,
                        c.page_number,
                        c.ordinal,
                        c.text,
                        c.char_start,
                        c.char_end,
                    )
                    for c in chunks
                ],
            )

        return DocumentRead(
            id=document_id,
            filename=filename,
            media_type=media_type,
            sha256=sha256,
            page_count=len(pages),
            chunk_count=len(chunks),
            created_at=created_at,
        )

    def list_documents(self) -> list[DocumentRead]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM documents ORDER BY created_at DESC").fetchall()
        return [DocumentRead(**dict(row)) for row in rows]

    def get_document(self, document_id: str) -> DocumentRead | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM documents WHERE id = ?", (document_id,)).fetchone()
        return DocumentRead(**dict(row)) if row else None

    def get_page(self, document_id: str, page_number: int) -> PageRead | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM pages WHERE document_id = ? AND page_number = ?",
                (document_id, page_number),
            ).fetchone()
        return PageRead(**dict(row)) if row else None

    def list_chunks(self, document_id: str) -> list[ChunkRead]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM chunks
                WHERE document_id = ?
                ORDER BY page_number, ordinal
                """,
                (document_id,),
            ).fetchall()
        return [ChunkRead(**dict(row)) for row in rows]

    def delete_document(self, document_id: str) -> bool:
        with self._connect() as conn:
            cursor = conn.execute("DELETE FROM documents WHERE id = ?", (document_id,))
        return cursor.rowcount > 0

    def get_chunk(self, chunk_id: str) -> ChunkRead | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM chunks WHERE id = ?", (chunk_id,)).fetchone()
        return ChunkRead(**dict(row)) if row else None

    def find_document_by_hash(self, sha256: str) -> DocumentRead | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM documents WHERE sha256 = ? LIMIT 1", (sha256,)
            ).fetchone()
        return DocumentRead(**dict(row)) if row else None
