"""One explicit pipeline entry per native opportunity, with serialized revision checks."""

import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from uuid import UUID

from pydantic import ValidationError

from tendercite.domain.opportunities import OpportunityError, OpportunityMetadata
from tendercite.domain.pipeline import (
    PipelineCreate,
    PipelineEntry,
    PipelineError,
    PipelineErrorCode,
    PipelineListing,
    PipelineRecord,
    PipelineSaveResult,
    PipelineStage,
    PipelineUpdate,
    PipelineValues,
)

PIPELINE_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS native_pipeline (
    pipeline_id TEXT PRIMARY KEY NOT NULL,
    opportunity_id TEXT NOT NULL UNIQUE REFERENCES native_opportunities(opportunity_id),
    stage TEXT NOT NULL CHECK(stage IN
        ('SAVED','REVIEWING','PREPARING','SUBMITTED','WON','LOST','ARCHIVED')),
    revision INTEGER NOT NULL CHECK(revision BETWEEN 1 AND 2147483647),
    payload TEXT NOT NULL CHECK(length(CAST(payload AS BLOB)) BETWEEN 1 AND 65536)
);
CREATE INDEX IF NOT EXISTS native_pipeline_stage_id ON native_pipeline(stage, pipeline_id);
"""


class PipelineRepositoryMixin:
    @contextmanager
    def _pipeline_transaction(self, *, write=False):
        if write and self.read_only:
            raise PipelineError(PipelineErrorCode.DATABASE_FAILED)
        try:
            with self._connect() as conn:
                conn.execute("BEGIN IMMEDIATE" if write else "BEGIN")
                yield conn
        except sqlite3.Error:
            raise PipelineError(PipelineErrorCode.DATABASE_FAILED) from None
        except OpportunityError as exc:
            raise PipelineError(PipelineErrorCode(exc.code.value)) from None

    @staticmethod
    def _pipeline_input(value, contract):
        try:
            return contract.model_validate(value)
        except ValidationError:
            raise PipelineError(PipelineErrorCode.INVALID_INPUT) from None

    @staticmethod
    def _pipeline_id(value):
        if not isinstance(value, UUID):
            raise PipelineError(PipelineErrorCode.INVALID_INPUT)
        return str(value)

    @staticmethod
    def _stored_pipeline(row):
        if row is None:
            raise PipelineError(PipelineErrorCode.NOT_FOUND)
        try:
            record = PipelineRecord.model_validate_json(row["payload"])
        except (ValidationError, ValueError):
            raise PipelineError(PipelineErrorCode.STORAGE_INTEGRITY) from None
        if (
            str(record.pipeline_id) != row["pipeline_id"]
            or str(record.opportunity_id) != row["opportunity_id"]
            or record.revision != row["revision"]
            or record.values.stage != row["stage"]
        ):
            raise PipelineError(PipelineErrorCode.STORAGE_INTEGRITY)
        return record

    def _pipeline_entry(self, conn, row):
        record = self._stored_pipeline(row)
        source = self._stored_opportunity(
            conn.execute(
                "SELECT * FROM native_opportunities WHERE opportunity_id=?",
                (str(record.opportunity_id),),
            ).fetchone()
        )
        return PipelineEntry(
            record=record,
            opportunity=OpportunityMetadata(
                opportunity_id=source.opportunity_id,
                revision=source.revision,
                title=source.opportunity.title,
                opportunity_type=source.opportunity.opportunity_type,
                organization=source.opportunity.organization,
                deadline=source.opportunity.deadline,
                updated_at=source.updated_at,
            ),
        )

    @staticmethod
    def _pipeline_payload(record):
        payload = record.model_dump_json()
        if len(payload.encode("utf-8")) > 65536:
            raise PipelineError(PipelineErrorCode.INVALID_INPUT)
        return payload

    def get_pipeline_entry(self, pipeline_id: UUID) -> PipelineEntry:
        identity = self._pipeline_id(pipeline_id)
        with self._pipeline_transaction() as conn:
            return self._pipeline_entry(
                conn,
                conn.execute(
                    "SELECT * FROM native_pipeline WHERE pipeline_id=?",
                    (identity,),
                ).fetchone(),
            )

    def list_pipeline(
        self,
        *,
        stage: PipelineStage | None = None,
        opportunity_id: UUID | None = None,
        limit=50,
        offset=0,
    ):
        opportunity = self._pipeline_id(opportunity_id) if opportunity_id is not None else None
        if (
            type(limit) is not int
            or not 1 <= limit <= 100
            or type(offset) is not int
            or not 0 <= offset <= 1000000
            or (stage is not None and not isinstance(stage, PipelineStage))
        ):
            raise PipelineError(PipelineErrorCode.INVALID_INPUT)
        with self._pipeline_transaction() as conn:
            clauses, params = [], []
            if stage is not None:
                clauses.append("stage=?")
                params.append(stage.value)
            if opportunity is not None:
                clauses.append("opportunity_id=?")
                params.append(opportunity)
            where = "WHERE " + " AND ".join(clauses) if clauses else ""
            rows = conn.execute(
                f"SELECT * FROM native_pipeline {where} ORDER BY pipeline_id LIMIT ? OFFSET ?",
                (*params, limit + 1, offset),
            ).fetchall()
            return PipelineListing(
                entries=tuple(self._pipeline_entry(conn, r) for r in rows[:limit]),
                next_offset=offset + limit if len(rows) > limit else None,
            )

    def create_pipeline_entry(self, request: PipelineCreate) -> PipelineSaveResult:
        request = self._pipeline_input(request, PipelineCreate)
        identity, opportunity = str(request.pipeline_id), str(request.opportunity_id)
        with self._pipeline_transaction(write=True) as conn:
            self._stored_opportunity(
                conn.execute(
                    "SELECT * FROM native_opportunities WHERE opportunity_id=?",
                    (opportunity,),
                ).fetchone()
            )
            by_id = conn.execute(
                "SELECT * FROM native_pipeline WHERE pipeline_id=?", (identity,)
            ).fetchone()
            if by_id and str(self._stored_pipeline(by_id).opportunity_id) != opportunity:
                raise PipelineError(PipelineErrorCode.ID_CONFLICT)
            existing = conn.execute(
                "SELECT * FROM native_pipeline WHERE opportunity_id=?", (opportunity,)
            ).fetchone()
            if existing:
                return PipelineSaveResult(
                    status="ALREADY_PRESENT", entry=self._pipeline_entry(conn, existing)
                )
            now = datetime.now(UTC)
            record = PipelineRecord(
                pipeline_id=request.pipeline_id,
                opportunity_id=request.opportunity_id,
                revision=1,
                created_at=now,
                updated_at=now,
                values=PipelineValues(),
            )
            conn.execute(
                "INSERT INTO native_pipeline VALUES (?, ?, ?, ?, ?)",
                (identity, opportunity, record.values.stage, 1, self._pipeline_payload(record)),
            )
            return PipelineSaveResult(
                status="CREATED",
                entry=self._pipeline_entry(
                    conn,
                    conn.execute(
                        "SELECT * FROM native_pipeline WHERE pipeline_id=?", (identity,)
                    ).fetchone(),
                ),
            )

    def update_pipeline_entry(
        self, pipeline_id: UUID, request: PipelineUpdate
    ) -> PipelineSaveResult:
        identity = self._pipeline_id(pipeline_id)
        request = self._pipeline_input(request, PipelineUpdate)
        with self._pipeline_transaction(write=True) as conn:
            current = self._stored_pipeline(
                conn.execute(
                    "SELECT * FROM native_pipeline WHERE pipeline_id=?",
                    (identity,),
                ).fetchone()
            )
            if current.revision != request.expected_revision or current.revision == 2147483647:
                raise PipelineError(PipelineErrorCode.REVISION_CONFLICT)
            record = PipelineRecord(
                pipeline_id=current.pipeline_id,
                opportunity_id=current.opportunity_id,
                revision=current.revision + 1,
                created_at=current.created_at,
                updated_at=max(datetime.now(UTC), current.updated_at),
                values=request.values,
            )
            cursor = conn.execute(
                "UPDATE native_pipeline SET stage=?, revision=?, payload=? "
                "WHERE pipeline_id=? AND revision=?",
                (
                    record.values.stage,
                    record.revision,
                    self._pipeline_payload(record),
                    identity,
                    request.expected_revision,
                ),
            )
            if cursor.rowcount != 1:
                raise PipelineError(PipelineErrorCode.REVISION_CONFLICT)
            return PipelineSaveResult(
                status="UPDATED",
                entry=self._pipeline_entry(
                    conn,
                    conn.execute(
                        "SELECT * FROM native_pipeline WHERE pipeline_id=?", (identity,)
                    ).fetchone(),
                ),
            )
