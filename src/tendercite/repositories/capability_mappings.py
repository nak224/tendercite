"""Mapping methods use the parent repository connection, never a second database."""

import sqlite3
from contextlib import contextmanager

from pydantic import ValidationError

from tendercite.domain.analysis import AnalysisRun
from tendercite.domain.capability_mapping_records import (
    CapabilityMappingError,
    CapabilityMappingRecord,
    MappingErrorCode,
    MappingInspection,
    MappingLifecycle,
    MappingStatus,
    MappingUnavailableReason,
    RequirementInterpretation,
    RequirementReviewBinding,
    reviewed_value_digest,
)
from tendercite.domain.models import Finding, GroundingStatus, ReviewStatus
from tendercite.domain.review import ReviewEvent


def _binding(finding, event):
    value = RequirementInterpretation.model_validate_json(finding.effective_value.model_dump_json())
    return RequirementReviewBinding(
        review_event_id=event.id if event else None,
        value=value,
        digest=reviewed_value_digest(value),
    )


def _eligible(finding, event):
    return (
        event is not None
        and finding.review_status in (ReviewStatus.CONFIRMED, ReviewStatus.MODIFIED)
        and finding.grounding_status == GroundingStatus.VERIFIED_QUOTE
    )


def _check_binding(binding, request):
    if (binding.review_event_id, binding.digest) != (
        request.expected_review_event_id,
        request.expected_review_digest,
    ):
        raise CapabilityMappingError(MappingErrorCode.REVIEW_CONFLICT)


class CapabilityMappingRepositoryMixin:
    @contextmanager
    def _mapping_transaction(self, *, write=False):
        if write and self.read_only:
            raise CapabilityMappingError(MappingErrorCode.DATABASE_FAILED)
        try:
            with self._connect() as conn:
                conn.execute("BEGIN IMMEDIATE" if write else "BEGIN")
                yield conn
        except sqlite3.IntegrityError:
            raise CapabilityMappingError(MappingErrorCode.CONFLICT) from None
        except sqlite3.Error:
            raise CapabilityMappingError(MappingErrorCode.DATABASE_FAILED) from None

    @staticmethod
    def _validate_mapping_input(request, contract):
        try:
            return contract.model_validate(request)
        except ValidationError:
            raise CapabilityMappingError(MappingErrorCode.INVALID_INPUT) from None

    def _mapping_sources(self, conn, analysis_id, finding_id, snapshot_hash):
        analysis_row = conn.execute(
            "SELECT payload FROM analyses WHERE id=?", (analysis_id,)
        ).fetchone()
        finding_row = conn.execute(
            "SELECT payload, analysis_run_id FROM findings WHERE id=?", (finding_id,)
        ).fetchone()
        if not analysis_row or not finding_row or finding_row["analysis_run_id"] != analysis_id:
            raise CapabilityMappingError(MappingErrorCode.INVALID_SCOPE)
        analysis = AnalysisRun.model_validate_json(analysis_row["payload"])
        finding = Finding.model_validate_json(finding_row["payload"])
        if (
            analysis.id != analysis_id
            or finding.id != finding_id
            or finding.analysis_run_id != analysis_id
            or finding_id not in {f.id for f in analysis.findings}
        ):
            raise CapabilityMappingError(MappingErrorCode.INVALID_SCOPE)
        review_row = conn.execute(
            "SELECT sequence, payload FROM reviews WHERE finding_id=? "
            "ORDER BY sequence DESC LIMIT 1",
            (finding_id,),
        ).fetchone()
        event = ReviewEvent.model_validate_json(review_row["payload"]) if review_row else None
        if event:
            if event.finding_id != finding_id:
                raise CapabilityMappingError(MappingErrorCode.STORAGE_INTEGRITY)
            finding.review_status = {
                "CONFIRM": ReviewStatus.CONFIRMED,
                "MODIFY": ReviewStatus.MODIFIED,
                "REJECT": ReviewStatus.REJECTED,
            }[event.action]
            finding.reviewed_value = event.reviewed_value
        row = conn.execute(
            "SELECT * FROM capability_snapshots WHERE content_hash=?", (snapshot_hash,)
        ).fetchone()
        if not row:
            raise CapabilityMappingError(MappingErrorCode.SNAPSHOT_NOT_FOUND)
        return analysis, finding, event, self._stored_capability(row), review_row

    @staticmethod
    def _mapping_row(conn, mapping_id):
        return conn.execute(
            "SELECT m.*, r.revision, r.status, r.review_sequence, r.payload "
            "FROM capability_mappings m "
            "JOIN capability_mapping_revisions r USING(mapping_id) WHERE m.mapping_id=? "
            "ORDER BY r.revision DESC LIMIT 1",
            (str(mapping_id),),
        ).fetchone()

    @staticmethod
    def _stored_mapping(conn, row):
        try:
            record = CapabilityMappingRecord.model_validate_json(row["payload"])
            if (
                str(record.mapping_id) != row["mapping_id"]
                or record.analysis_id != row["analysis_id"]
                or record.finding_id != row["finding_id"]
                or record.snapshot_hash != row["snapshot_hash"]
                or record.revision != row["revision"]
                or record.status != row["status"]
                or record.created_at.isoformat() != row["created_at"]
            ):
                raise ValueError("Invalid mapping metadata")
            if row["review_sequence"] is None:
                if record.review_binding.review_event_id is not None:
                    raise ValueError("Missing review association")
            else:
                review = conn.execute(
                    "SELECT payload FROM reviews WHERE sequence=?", (row["review_sequence"],)
                ).fetchone()
                event = ReviewEvent.model_validate_json(review["payload"])
                if (
                    event.id != record.review_binding.review_event_id
                    or event.finding_id != record.finding_id
                    or reviewed_value_digest(event.reviewed_value) != record.review_binding.digest
                ):
                    raise ValueError("Invalid review association")
            return record
        except (ValueError, TypeError, KeyError, AttributeError):
            raise CapabilityMappingError(MappingErrorCode.STORAGE_INTEGRITY) from None

    def _inspection(self, conn, analysis_id, finding_id, snapshot_hash, sources=None):
        _, finding, event, snapshot, _ = sources or self._mapping_sources(
            conn, analysis_id, finding_id, snapshot_hash
        )
        row = conn.execute(
            "SELECT mapping_id FROM capability_mappings "
            "WHERE analysis_id=? AND finding_id=? AND snapshot_hash=?",
            (analysis_id, finding_id, snapshot_hash),
        ).fetchone()
        record = (
            self._stored_mapping(conn, self._mapping_row(conn, row["mapping_id"])) if row else None
        )
        reasons = []
        if record is None:
            status, reasons = MappingStatus.NOT_FOUND, [MappingUnavailableReason.NO_MAPPING]
        elif record.status != MappingLifecycle.APPROVED:
            status = MappingStatus(record.status.value)
            reasons = [
                MappingUnavailableReason.DRAFT
                if status == MappingStatus.DRAFT
                else MappingUnavailableReason.REVOKED
            ]
        else:
            current = _binding(finding, event)
            if current.review_event_id != record.review_binding.review_event_id:
                reasons.append(MappingUnavailableReason.REVIEW_CHANGED)
            if current.digest != record.review_binding.digest:
                reasons.append(MappingUnavailableReason.VALUE_CHANGED)
            if event is None or finding.review_status not in (
                ReviewStatus.CONFIRMED,
                ReviewStatus.MODIFIED,
            ):
                reasons.append(MappingUnavailableReason.INELIGIBLE_REVIEW)
            if finding.grounding_status != GroundingStatus.VERIFIED_QUOTE:
                reasons.append(MappingUnavailableReason.INVALID_GROUNDING)
            status = MappingStatus.STALE if reasons else MappingStatus.APPROVED
        if record and not set(record.skill_ids) <= {s.skill_id for s in snapshot.skill_catalog}:
            raise CapabilityMappingError(MappingErrorCode.STORAGE_INTEGRITY)
        return MappingInspection(
            analysis_id=analysis_id,
            finding_id=finding_id,
            snapshot_hash=snapshot_hash,
            status=status,
            record=record,
            reasons=tuple(reasons),
        )

    def get_mapping_review_binding(self, analysis_id, finding_id, snapshot_hash):
        with self._mapping_transaction() as conn:
            _, finding, event, _, _ = self._mapping_sources(
                conn, analysis_id, finding_id, snapshot_hash
            )
            return _binding(finding, event)

    def get_capability_mapping(self, mapping_id):
        with self._mapping_transaction() as conn:
            row = self._mapping_row(conn, mapping_id)
            if row is None:
                return None
            return self._inspection(
                conn, row["analysis_id"], row["finding_id"], row["snapshot_hash"]
            )

    def capability_mapping_history(self, mapping_id):
        with self._mapping_transaction() as conn:
            rows = conn.execute(
                "SELECT m.*, r.revision, r.status, r.review_sequence, r.payload "
                "FROM capability_mappings m JOIN capability_mapping_revisions r USING(mapping_id) "
                "WHERE m.mapping_id=? ORDER BY r.revision",
                (str(mapping_id),),
            ).fetchall()
            return [self._stored_mapping(conn, row) for row in rows]
