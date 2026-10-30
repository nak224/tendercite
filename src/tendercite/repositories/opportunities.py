"""Independent opportunity CRUD following native profile save/revision conventions."""

import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from uuid import UUID

from pydantic import ValidationError

from tendercite.domain.opportunities import (
    MAX_OPPORTUNITY_BYTES,
    NativeOpportunity,
    OpportunityCreate,
    OpportunityError,
    OpportunityErrorCode,
    OpportunityListing,
    OpportunityMetadata,
    OpportunityRecord,
    OpportunitySaveResult,
    OpportunityUpdate,
)
from tendercite.domain.ted_reference import publication_id, ted_opportunity_id

OPPORTUNITY_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS native_opportunities (
    opportunity_id TEXT PRIMARY KEY NOT NULL,
    revision INTEGER NOT NULL CHECK(revision BETWEEN 1 AND 2147483647),
    payload TEXT NOT NULL CHECK(length(CAST(payload AS BLOB)) BETWEEN 1 AND 65536)
);
"""


class OpportunityRepositoryMixin:
    @contextmanager
    def _opportunity_transaction(self, *, write=False):
        if write and self.read_only:
            raise OpportunityError(OpportunityErrorCode.DATABASE_FAILED)
        try:
            with self._connect() as conn:
                conn.execute("BEGIN IMMEDIATE" if write else "BEGIN")
                yield conn
        except sqlite3.Error:
            raise OpportunityError(OpportunityErrorCode.DATABASE_FAILED) from None

    @staticmethod
    def _opportunity_id(opportunity_id):
        if not isinstance(opportunity_id, UUID):
            raise OpportunityError(OpportunityErrorCode.INVALID_INPUT)
        return str(opportunity_id)

    @staticmethod
    def _opportunity_input(value, contract):
        try:
            return contract.model_validate(value)
        except ValidationError:
            raise OpportunityError(OpportunityErrorCode.INVALID_INPUT) from None

    @staticmethod
    def _stored_opportunity(row):
        if row is None:
            raise OpportunityError(OpportunityErrorCode.NOT_FOUND)
        try:
            record = OpportunityRecord.model_validate_json(row["payload"])
        except (ValidationError, ValueError):
            raise OpportunityError(OpportunityErrorCode.STORAGE_INTEGRITY) from None
        if (
            str(record.opportunity_id) != row["opportunity_id"]
            or record.revision != row["revision"]
        ):
            raise OpportunityError(OpportunityErrorCode.STORAGE_INTEGRITY)
        return record

    @staticmethod
    def _opportunity_payload(record):
        payload = record.model_dump_json()
        if len(payload.encode("utf-8")) > MAX_OPPORTUNITY_BYTES:
            raise OpportunityError(OpportunityErrorCode.INVALID_INPUT)
        return payload

    def get_opportunity(self, opportunity_id: UUID) -> OpportunityRecord:
        identity = self._opportunity_id(opportunity_id)
        with self._opportunity_transaction() as conn:
            return self._stored_opportunity(
                conn.execute(
                    "SELECT * FROM native_opportunities WHERE opportunity_id=?", (identity,)
                ).fetchone()
            )

    def list_opportunities(self, *, limit=50, offset=0) -> OpportunityListing:
        if (
            type(limit) is not int
            or not 1 <= limit <= 100
            or type(offset) is not int
            or not 0 <= offset <= 1000000
        ):
            raise OpportunityError(OpportunityErrorCode.INVALID_INPUT)
        with self._opportunity_transaction() as conn:
            rows = conn.execute(
                "SELECT * FROM native_opportunities ORDER BY opportunity_id LIMIT ? OFFSET ?",
                (limit + 1, offset),
            ).fetchall()
            records = [self._stored_opportunity(row) for row in rows[:limit]]
            return OpportunityListing(
                opportunities=tuple(
                    OpportunityMetadata(
                        opportunity_id=r.opportunity_id,
                        revision=r.revision,
                        title=r.opportunity.title,
                        opportunity_type=r.opportunity.opportunity_type,
                        organization=r.opportunity.organization,
                        deadline=r.opportunity.deadline,
                        updated_at=r.updated_at,
                    )
                    for r in records
                ),
                next_offset=offset + limit if len(rows) > limit else None,
            )

    def create_opportunity(self, request: OpportunityCreate) -> OpportunitySaveResult:
        request = self._opportunity_input(request, OpportunityCreate)
        identity = str(request.opportunity_id)
        with self._opportunity_transaction(write=True) as conn:
            row = conn.execute(
                "SELECT * FROM native_opportunities WHERE opportunity_id=?", (identity,)
            ).fetchone()
            if row:
                record = self._stored_opportunity(row)
                if record.revision != 1 or record.opportunity != request.opportunity:
                    raise OpportunityError(OpportunityErrorCode.ID_CONFLICT)
                return OpportunitySaveResult(status="ALREADY_PRESENT", record=record)
            return self._insert_opportunity(conn, request.opportunity_id, request.opportunity)

    def _insert_opportunity(self, conn, identity, opportunity):
        now = datetime.now(UTC)
        record = OpportunityRecord(
            opportunity_id=identity,
            revision=1,
            created_at=now,
            updated_at=now,
            opportunity=opportunity,
        )
        conn.execute(
            "INSERT INTO native_opportunities VALUES (?, ?, ?)",
            (str(identity), 1, self._opportunity_payload(record)),
        )
        return OpportunitySaveResult(status="CREATED", record=record)

    def _ted_existing(self, conn, notice_id):
        identity = ted_opportunity_id(notice_id)
        row = conn.execute(
            "SELECT * FROM native_opportunities WHERE opportunity_id=?", (str(identity),)
        ).fetchone()
        if row is None:
            return None
        record = self._stored_opportunity(row)
        source = record.opportunity.external_source
        if (
            record.opportunity.source_origin != "ted_import"
            or source is None
            or source.external_id != notice_id
        ):
            raise OpportunityError(OpportunityErrorCode.ID_CONFLICT)
        return OpportunitySaveResult(status="ALREADY_PRESENT", record=record)

    def find_ted_opportunity(self, notice_id: str) -> OpportunitySaveResult | None:
        try:
            notice_id = publication_id(notice_id)
        except ValueError:
            raise OpportunityError(OpportunityErrorCode.INVALID_INPUT) from None
        with self._opportunity_transaction() as conn:
            return self._ted_existing(conn, notice_id)

    def import_ted_opportunity(self, opportunity: NativeOpportunity) -> OpportunitySaveResult:
        opportunity = self._opportunity_input(opportunity, NativeOpportunity)
        if opportunity.source_origin != "ted_import" or opportunity.external_source is None:
            raise OpportunityError(OpportunityErrorCode.INVALID_INPUT)
        notice_id = opportunity.external_source.external_id
        with self._opportunity_transaction(write=True) as conn:
            if current := self._ted_existing(conn, notice_id):
                return current
            return self._insert_opportunity(conn, ted_opportunity_id(notice_id), opportunity)

    def update_opportunity(
        self, opportunity_id: UUID, request: OpportunityUpdate
    ) -> OpportunitySaveResult:
        identity = self._opportunity_id(opportunity_id)
        request = self._opportunity_input(request, OpportunityUpdate)
        with self._opportunity_transaction(write=True) as conn:
            current = self._stored_opportunity(
                conn.execute(
                    "SELECT * FROM native_opportunities WHERE opportunity_id=?", (identity,)
                ).fetchone()
            )
            if current.revision != request.expected_revision or current.revision == 2147483647:
                raise OpportunityError(OpportunityErrorCode.REVISION_CONFLICT)
            if (
                current.opportunity.source_origin != request.opportunity.source_origin
                or current.opportunity.external_source != request.opportunity.external_source
            ):
                raise OpportunityError(OpportunityErrorCode.PROVENANCE_CONFLICT)
            record = OpportunityRecord(
                opportunity_id=opportunity_id,
                revision=current.revision + 1,
                created_at=current.created_at,
                updated_at=max(datetime.now(UTC), current.updated_at),
                opportunity=request.opportunity,
            )
            cursor = conn.execute(
                "UPDATE native_opportunities SET revision=?, payload=? "
                "WHERE opportunity_id=? AND revision=?",
                (
                    record.revision,
                    self._opportunity_payload(record),
                    identity,
                    request.expected_revision,
                ),
            )
            if cursor.rowcount != 1:
                raise OpportunityError(OpportunityErrorCode.REVISION_CONFLICT)
            return OpportunitySaveResult(status="UPDATED", record=record)
