"""Atomic manual intake on the existing connection and ordinary analysis/finding tables."""

import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime

from pydantic import TypeAdapter, ValidationError

from tendercite.domain.analysis import AnalysisRun
from tendercite.domain.curated_intake import (
    CuratedIntakeError,
    Identifier,
    IntakeErrorCode,
    intake_analysis_id,
)
from tendercite.domain.models import DocumentRead, Finding, PageRead
from tendercite.services.curated_intake import (
    build_curated_analysis,
    checked_intake,
    intake_summary,
    validate_intake_sources,
)


class CuratedIntakeRepositoryMixin:
    @contextmanager
    def _intake_transaction(self, *, write=False):
        if write and self.read_only:
            raise CuratedIntakeError(IntakeErrorCode.DATABASE_FAILED)
        try:
            with self._connect() as conn:
                conn.execute("BEGIN IMMEDIATE" if write else "BEGIN")
                yield conn
        except sqlite3.IntegrityError:
            raise CuratedIntakeError(IntakeErrorCode.IDENTITY_CONFLICT) from None
        except sqlite3.Error:
            raise CuratedIntakeError(IntakeErrorCode.DATABASE_FAILED) from None
        except ValidationError:
            raise CuratedIntakeError(IntakeErrorCode.STORAGE_INTEGRITY) from None

    @staticmethod
    def _validated_intake(conn, intake):
        documents, pages = {}, {}
        for source in intake.documents:
            row = conn.execute(
                "SELECT * FROM documents WHERE id=?", (source.document_id,)
            ).fetchone()
            if row:
                documents[source.document_id] = DocumentRead(**dict(row))
            rows = conn.execute(
                "SELECT * FROM pages WHERE document_id=? ORDER BY page_number",
                (source.document_id,),
            )
            for row in rows:
                page = PageRead(**dict(row))
                pages[(page.document_id, page.page_number)] = page
        return validate_intake_sources(intake, documents=documents, pages=pages)

    def validate_curated_intake(self, intake):
        intake = checked_intake(intake)
        with self._intake_transaction() as conn:
            self._validated_intake(conn, intake)
            return intake_summary(intake, status="VALIDATED")

    @staticmethod
    def _check_existing_intake(conn, existing, validated):
        intake = validated.provenance.intake
        if existing.id != intake_analysis_id(intake) or existing.curated_intake is None:
            raise CuratedIntakeError(IntakeErrorCode.IDENTITY_CONFLICT)
        if existing.curated_intake.intake != intake:
            raise CuratedIntakeError(IntakeErrorCode.IDENTITY_CONFLICT)
        if existing.curated_intake.validated_pages != validated.provenance.validated_pages:
            raise CuratedIntakeError(IntakeErrorCode.SOURCE_CHANGED)
        expected = build_curated_analysis(validated, created_at=existing.created_at)
        if existing.model_dump(mode="json") != expected.model_dump(mode="json"):
            raise CuratedIntakeError(IntakeErrorCode.STORAGE_INTEGRITY)
        rows = conn.execute(
            "SELECT id, payload FROM findings WHERE analysis_run_id=?", (existing.id,)
        ).fetchall()
        stored = {
            r["id"]: Finding.model_validate_json(r["payload"]).model_dump(mode="json") for r in rows
        }
        if stored != {f.id: f.model_dump(mode="json") for f in expected.findings}:
            raise CuratedIntakeError(IntakeErrorCode.STORAGE_INTEGRITY)

    def commit_curated_intake(self, intake):
        intake = checked_intake(intake)
        with self._intake_transaction(write=True) as conn:
            # Revalidate sources inside the same write transaction as insertion/idempotency.
            validated = self._validated_intake(conn, intake)
            row = conn.execute(
                "SELECT payload FROM analyses WHERE id=?", (intake_analysis_id(intake),)
            ).fetchone()
            if row:
                existing = AnalysisRun.model_validate_json(row["payload"])
                self._check_existing_intake(conn, existing, validated)
                return intake_summary(intake, status="ALREADY_PRESENT")
            run = build_curated_analysis(validated, created_at=datetime.now(UTC))
            self._insert_analysis(conn, run)
            return intake_summary(intake, status="CREATED")

    def inspect_curated_intake(self, analysis_id):
        try:
            analysis_id = TypeAdapter(Identifier).validate_python(analysis_id, strict=True)
        except ValidationError:
            raise CuratedIntakeError(IntakeErrorCode.INVALID_INPUT) from None
        with self._intake_transaction() as conn:
            row = conn.execute("SELECT payload FROM analyses WHERE id=?", (analysis_id,)).fetchone()
            if not row:
                raise CuratedIntakeError(IntakeErrorCode.ANALYSIS_NOT_FOUND)
            run = AnalysisRun.model_validate_json(row["payload"])
            if run.curated_intake is None:
                raise CuratedIntakeError(IntakeErrorCode.NOT_MANUAL)
            intake = run.curated_intake.intake
            if run.id != analysis_id or run.id != intake_analysis_id(intake):
                raise CuratedIntakeError(IntakeErrorCode.STORAGE_INTEGRITY)
            return run, intake_summary(intake, status="INSPECTED")
