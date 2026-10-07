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

                CREATE TABLE IF NOT EXISTS analyses (
                    id TEXT PRIMARY KEY,
                    payload TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS findings (
                    id TEXT PRIMARY KEY,
                    analysis_run_id TEXT NOT NULL REFERENCES analyses(id),
                    payload TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS reviews (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    finding_id TEXT NOT NULL REFERENCES findings(id),
                    payload TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_reviews_finding ON reviews(finding_id, sequence);
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

    def save_analysis(self, run) -> None:
        with self._connect() as conn:
            conn.execute("INSERT INTO analyses VALUES (?, ?)", (run.id, run.model_dump_json()))
            conn.executemany(
                "INSERT INTO findings VALUES (?, ?, ?)",
                [(f.id, run.id, f.model_dump_json()) for f in run.findings],
            )

    def get_analysis(self, analysis_id: str):
        from tendercite.domain.analysis import AnalysisRun

        with self._connect() as conn:
            row = conn.execute(
                "SELECT payload FROM analyses WHERE id = ?", (analysis_id,)
            ).fetchone()
        return AnalysisRun.model_validate_json(row["payload"]) if row else None

    def list_findings(self, analysis_run_id: str | None = None):
        from tendercite.domain.models import Finding

        with self._connect() as conn:
            rows = conn.execute(
                "SELECT payload FROM findings WHERE (? IS NULL OR analysis_run_id=?) "
                "ORDER BY rowid",
                (analysis_run_id, analysis_run_id),
            ).fetchall()
        return [self._apply_review(Finding.model_validate_json(row["payload"])) for row in rows]

    def get_finding(self, finding_id: str):
        from tendercite.domain.models import Finding

        with self._connect() as conn:
            row = conn.execute(
                "SELECT payload FROM findings WHERE id = ?", (finding_id,)
            ).fetchone()
        return self._apply_review(Finding.model_validate_json(row["payload"])) if row else None

    def document_has_analyses(self, document_id: str) -> bool:
        import json

        with self._connect() as conn:
            rows = conn.execute("SELECT payload FROM analyses").fetchall()
        return any(
            document_id in json.loads(row["payload"])["request"]["document_ids"] for row in rows
        )

    def list_reviews(self, finding_id: str):
        from tendercite.domain.review import ReviewEvent

        with self._connect() as conn:
            rows = conn.execute(
                "SELECT payload FROM reviews WHERE finding_id=? ORDER BY sequence", (finding_id,)
            ).fetchall()
        return [ReviewEvent.model_validate_json(row["payload"]) for row in rows]

    def _apply_review(self, finding):
        from tendercite.domain.models import ReviewStatus

        events = self.list_reviews(finding.id)
        if events:
            event = events[-1]
            finding.review_status = {
                "CONFIRM": ReviewStatus.CONFIRMED,
                "MODIFY": ReviewStatus.MODIFIED,
                "REJECT": ReviewStatus.REJECTED,
            }[event.action]
            finding.reviewed_value = event.reviewed_value
        return finding

    def add_review(self, finding_id: str, request):
        from uuid import uuid4

        from tendercite.domain.models import Finding, ReviewStatus
        from tendercite.domain.review import ReviewEvent

        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT payload FROM findings WHERE id=?", (finding_id,)).fetchone()
            if row is None:
                raise KeyError(finding_id)
            original = Finding.model_validate_json(row["payload"])
            latest = conn.execute(
                "SELECT payload FROM reviews WHERE finding_id=? ORDER BY sequence DESC LIMIT 1",
                (finding_id,),
            ).fetchone()
            previous = ReviewEvent.model_validate_json(latest["payload"]) if latest else None
            previous_value = previous.reviewed_value if previous else original.effective_value
            status = {
                "CONFIRM": ReviewStatus.CONFIRMED,
                "MODIFY": ReviewStatus.MODIFIED,
                "REJECT": ReviewStatus.REJECTED,
            }
            event = ReviewEvent(
                id=str(uuid4()),
                finding_id=finding_id,
                action=request.action,
                original_value=original.model_dump(mode="json"),
                previous_value=previous_value,
                reviewed_value=request.reviewed_value or previous_value,
                previous_status=status[previous.action] if previous else ReviewStatus.UNREVIEWED,
                comment=request.comment,
                created_at=datetime.now(UTC),
            )
            conn.execute(
                "INSERT INTO reviews(finding_id,payload) VALUES (?,?)",
                (finding_id, event.model_dump_json()),
            )
        return event
