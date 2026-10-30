import re
import sqlite3
from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from tendercite.domain.capabilities_v1 import CapabilitySnapshot, canonical_json
from tendercite.domain.capability_storage import CapabilityImportResult, CapabilitySnapshotMetadata
from tendercite.domain.models import ChunkRead, DocumentRead, PageRead
from tendercite.repositories.capability_mapping_schema import MAPPING_SCHEMA_SQL
from tendercite.repositories.capability_mappings import CapabilityMappingRepositoryMixin
from tendercite.repositories.curated_intake import CuratedIntakeRepositoryMixin
from tendercite.repositories.opportunities import OPPORTUNITY_SCHEMA_SQL, OpportunityRepositoryMixin
from tendercite.repositories.pipeline import PIPELINE_SCHEMA_SQL, PipelineRepositoryMixin
from tendercite.repositories.profiles import PROFILE_SCHEMA_SQL, ProfileRepositoryMixin
from tendercite.services.capabilities import (
    CapabilityError,
    CapabilityErrorCode,
    validate_capability_json,
)

MAX_CAPABILITY_LIST_LIMIT = 100


class SqliteRepository(
    CapabilityMappingRepositoryMixin,
    CuratedIntakeRepositoryMixin,
    ProfileRepositoryMixin,
    OpportunityRepositoryMixin,
    PipelineRepositoryMixin,
):
    def __init__(
        self, database_path: Path, *, read_only: bool = False, initialize: bool = True
    ) -> None:
        self.database_path = database_path
        self.read_only = read_only
        self.initialize = initialize
        if not read_only and initialize:
            self.database_path.parent.mkdir(parents=True, exist_ok=True)
            self._initialize()

    @contextmanager
    def _connect(self):
        connection = (
            sqlite3.connect(
                self.database_path.absolute().as_uri()
                + ("?mode=ro" if self.read_only else "?mode=rw"),
                uri=True,
                timeout=30,
            )
            if self.read_only or not self.initialize
            else sqlite3.connect(self.database_path, timeout=30)
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            with connection:
                yield connection
        finally:
            connection.close()

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
                CREATE TABLE IF NOT EXISTS assessments (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    finding_id TEXT NOT NULL REFERENCES findings(id),
                    review_event_id TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_chunks_document ON chunks(document_id);
                CREATE INDEX IF NOT EXISTS idx_chunks_page ON chunks(document_id, page_number);
                CREATE TABLE IF NOT EXISTS capability_snapshots (
                    content_hash TEXT PRIMARY KEY NOT NULL
                        CHECK(length(content_hash)=71 AND substr(content_hash,1,7)='sha256:'
                            AND substr(content_hash,8) NOT GLOB '*[^0-9a-f]*'),
                    schema_version TEXT NOT NULL CHECK(schema_version='1.0'),
                    source_system TEXT NOT NULL CHECK(source_system='team-capacity-engine'),
                    assessment_period_start TEXT NOT NULL CHECK(length(assessment_period_start)=10),
                    assessment_period_end TEXT NOT NULL
                        CHECK(length(assessment_period_end)=10
                            AND assessment_period_end>=assessment_period_start),
                    canonical_json TEXT NOT NULL
                        CHECK(length(CAST(canonical_json AS BLOB)) BETWEEN 1 AND 16777216),
                    imported_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_capabilities_imported
                    ON capability_snapshots(imported_at DESC, content_hash ASC);
                """
            )
            conn.executescript(MAPPING_SCHEMA_SQL)
            conn.executescript(PROFILE_SCHEMA_SQL)
            conn.executescript(OPPORTUNITY_SCHEMA_SQL)
            conn.executescript(PIPELINE_SCHEMA_SQL)

    def import_capability_snapshot(self, snapshot: CapabilitySnapshot) -> CapabilityImportResult:
        # Revalidate even programmatically constructed/copied models before starting a write.
        snapshot = validate_capability_json(canonical_json(snapshot))
        content = canonical_json(snapshot).decode("utf-8")
        period = snapshot.assessment_period
        if self.read_only:
            raise CapabilityError(CapabilityErrorCode.DATABASE_FAILED)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            cursor = conn.execute(
                """INSERT INTO capability_snapshots(
                    content_hash, schema_version, source_system, assessment_period_start,
                    assessment_period_end, canonical_json, imported_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(content_hash) DO NOTHING""",
                (
                    snapshot.content_hash,
                    snapshot.schema_version,
                    snapshot.source_system,
                    period.start_date.isoformat(),
                    period.end_date.isoformat(),
                    content,
                    datetime.now(UTC).isoformat(),
                ),
            )
            row = conn.execute(
                "SELECT * FROM capability_snapshots WHERE content_hash=?", (snapshot.content_hash,)
            ).fetchone()
            stored = self._stored_capability(row)
            if stored != snapshot or row["canonical_json"] != content:
                raise CapabilityError(CapabilityErrorCode.STORAGE_INTEGRITY)
            return CapabilityImportResult(cursor.rowcount == 1, self._capability_metadata(row))

    @staticmethod
    def _check_capability_hash(content_hash: str) -> None:
        if not isinstance(content_hash, str) or not re.fullmatch(
            r"sha256:[0-9a-f]{64}", content_hash
        ):
            raise CapabilityError(CapabilityErrorCode.INVALID_ARGUMENTS)

    @staticmethod
    def _capability_metadata(row) -> CapabilitySnapshotMetadata:
        try:
            imported = datetime.fromisoformat(row["imported_at"])
            start, end = (
                date.fromisoformat(row[k])
                for k in ("assessment_period_start", "assessment_period_end")
            )
            if (
                not re.fullmatch(r"sha256:[0-9a-f]{64}", row["content_hash"])
                or row["schema_version"] != "1.0"
                or row["source_system"] != "team-capacity-engine"
                or imported.utcoffset() != timedelta(0)
                or not 1 <= (end - start).days + 1 <= 366
                or start.isoformat() != row["assessment_period_start"]
                or end.isoformat() != row["assessment_period_end"]
            ):
                raise ValueError("Invalid snapshot metadata")
            return CapabilitySnapshotMetadata(
                row["content_hash"],
                row["schema_version"],
                row["source_system"],
                start,
                end,
                imported,
            )
        except (ValueError, TypeError, KeyError):
            raise CapabilityError(CapabilityErrorCode.STORAGE_INTEGRITY) from None

    @classmethod
    def _stored_capability(cls, row) -> CapabilitySnapshot:
        try:
            metadata = cls._capability_metadata(row)
            snapshot = validate_capability_json(row["canonical_json"].encode("utf-8"))
            if (
                canonical_json(snapshot).decode("utf-8") != row["canonical_json"]
                or snapshot.content_hash != metadata.content_hash
                or snapshot.schema_version != metadata.schema_version
                or snapshot.source_system != metadata.source_system
                or snapshot.assessment_period.start_date != metadata.assessment_period_start
                or snapshot.assessment_period.end_date != metadata.assessment_period_end
            ):
                raise CapabilityError(CapabilityErrorCode.STORAGE_INTEGRITY)
            return snapshot
        except (CapabilityError, ValueError, TypeError, AttributeError):
            raise CapabilityError(CapabilityErrorCode.STORAGE_INTEGRITY) from None

    def get_capability_snapshot(self, content_hash: str) -> CapabilitySnapshot | None:
        self._check_capability_hash(content_hash)
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM capability_snapshots WHERE content_hash=?", (content_hash,)
            ).fetchone()
        return self._stored_capability(row) if row else None

    def list_capability_snapshots(self, *, limit: int = 50) -> list[CapabilitySnapshotMetadata]:
        if type(limit) is not int or not 1 <= limit <= MAX_CAPABILITY_LIST_LIMIT:
            raise CapabilityError(CapabilityErrorCode.INVALID_ARGUMENTS)
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT content_hash, schema_version, source_system, assessment_period_start, "
                "assessment_period_end, imported_at FROM capability_snapshots "
                "ORDER BY imported_at DESC, content_hash ASC LIMIT ?",
                (limit,),
            ).fetchall()
        return [self._capability_metadata(row) for row in rows]

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
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute(
                "SELECT * FROM documents WHERE sha256=? LIMIT 1", (sha256,)
            ).fetchone()
            if existing:
                return DocumentRead(**dict(existing))
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
            conn.execute("BEGIN IMMEDIATE")
            if self.document_has_analyses(document_id):
                raise ValueError("Document is referenced by an analysis")
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
            conn.execute("BEGIN IMMEDIATE")
            self._insert_analysis(conn, run)

    @staticmethod
    def _insert_analysis(conn, run) -> None:
        """Share insertion with intake without opening an independent transaction."""
        for document_id in run.request.document_ids:
            if (
                conn.execute("SELECT id FROM documents WHERE id=?", (document_id,)).fetchone()
                is None
            ):
                raise ValueError("Source was deleted during analysis")
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

    def matrix_row(self, finding):
        from tendercite.domain.models import AssessmentRequest, GoNoGoRow, ReviewStatus

        if finding.review_status not in (ReviewStatus.CONFIRMED, ReviewStatus.MODIFIED):
            return None
        reviews = self.list_reviews(finding.id)
        with self._connect() as conn:
            assignment = conn.execute(
                "SELECT * FROM assessments WHERE finding_id=? ORDER BY sequence DESC LIMIT 1",
                (finding.id,),
            ).fetchone()
        values = {}
        if assignment and assignment["review_event_id"] == reviews[-1].id:
            values = AssessmentRequest.model_validate_json(assignment["payload"]).model_dump()
        return GoNoGoRow(
            finding_id=finding.id,
            criterion=finding.effective_value.statement,
            category=finding.effective_value.category,
            requirement_type=finding.effective_value.requirement_type,
            review_status=finding.review_status,
            grounding_status=finding.grounding_status,
            evidence=finding.evidence,
            **values,
        )

    def list_matrix(self, analysis_run_id: str | None = None):
        return [
            row
            for finding in self.list_findings(analysis_run_id)
            if (row := self.matrix_row(finding)) is not None
        ]

    def load_native_match_records(self, selection):
        """Read both native records and their revision guards in one SQLite snapshot."""
        from pydantic import ValidationError

        from tendercite.domain.native_matching import (
            NativeMatchError,
            NativeMatchErrorCode,
            NativeMatchSelection,
        )
        from tendercite.domain.opportunities import OpportunityError
        from tendercite.domain.profiles import ProfileError

        try:
            selection = NativeMatchSelection.model_validate(selection)
        except ValidationError:
            raise NativeMatchError(NativeMatchErrorCode.INVALID_INPUT) from None
        try:
            with self._connect() as conn:
                conn.execute("BEGIN")
                profile = self._stored_profile(
                    conn.execute(
                        "SELECT * FROM native_profiles WHERE profile_id=?",
                        (str(selection.profile_id),),
                    ).fetchone()
                )
                opportunity = self._stored_opportunity(
                    conn.execute(
                        "SELECT * FROM native_opportunities WHERE opportunity_id=?",
                        (str(selection.opportunity_id),),
                    ).fetchone()
                )
                if profile.revision != selection.expected_profile_revision:
                    raise NativeMatchError(NativeMatchErrorCode.PROFILE_CONFLICT)
                if opportunity.revision != selection.expected_opportunity_revision:
                    raise NativeMatchError(NativeMatchErrorCode.OPPORTUNITY_CONFLICT)
                return profile, opportunity
        except (ProfileError, OpportunityError) as exc:
            raise NativeMatchError(NativeMatchErrorCode(exc.code.value)) from None
        except sqlite3.Error:
            raise NativeMatchError(NativeMatchErrorCode.DATABASE_FAILED) from None

    def set_assessment(self, finding_id: str, request):
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            finding = self.get_finding(finding_id)
            if finding is None:
                raise KeyError(finding_id)
            if self.matrix_row(finding) is None:
                raise ValueError("Confirm or modify the finding before assessment")
            review = self.list_reviews(finding_id)[-1]
            conn.execute(
                "INSERT INTO assessments(finding_id,review_event_id,payload,created_at) "
                "VALUES (?,?,?,?)",
                (finding_id, review.id, request.model_dump_json(), datetime.now(UTC).isoformat()),
            )
        return self.matrix_row(self.get_finding(finding_id))
