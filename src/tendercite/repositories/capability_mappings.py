"""Mapping methods use the parent repository connection, never a second database."""

import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import uuid4

from pydantic import ValidationError

from tendercite.domain.analysis import AnalysisRun
from tendercite.domain.capabilities_v1 import CapabilitySnapshot
from tendercite.domain.capability_mapping_records import (
    CapabilityMappingError,
    CapabilityMappingRecord,
    MappingApprovalInput,
    MappingDraftInput,
    MappingErrorCode,
    MappingInspection,
    MappingLifecycle,
    MappingRevisionInput,
    MappingRevocationInput,
    MappingStatus,
    MappingUnavailableReason,
    RequirementInterpretation,
    RequirementReviewBinding,
    reviewed_value_digest,
)
from tendercite.domain.models import Finding, GroundingStatus, ReviewStatus
from tendercite.domain.review import ReviewEvent


@dataclass(frozen=True)
class CapabilityWorkflowState:
    """One read snapshot; internal composition data, never a persisted decision."""

    analysis: AnalysisRun
    snapshot: CapabilitySnapshot
    findings: tuple[Finding, ...]
    inspections: tuple[MappingInspection, ...]
    review_bindings: tuple[RequirementReviewBinding, ...]


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

    def _mapping_sources(self, conn, analysis_id, finding_id, snapshot_hash, *, cache=None):
        cache = {} if cache is None else cache
        if "analysis" not in cache:
            analysis_row = conn.execute(
                "SELECT payload FROM analyses WHERE id=?", (analysis_id,)
            ).fetchone()
            if not analysis_row:
                raise CapabilityMappingError(MappingErrorCode.INVALID_SCOPE)
            cache["analysis"] = AnalysisRun.model_validate_json(analysis_row["payload"])
        finding_row = conn.execute(
            "SELECT payload, analysis_run_id FROM findings WHERE id=?", (finding_id,)
        ).fetchone()
        if not finding_row or finding_row["analysis_run_id"] != analysis_id:
            raise CapabilityMappingError(MappingErrorCode.INVALID_SCOPE)
        analysis = cache["analysis"]
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
        if "snapshot" not in cache:
            row = conn.execute(
                "SELECT * FROM capability_snapshots WHERE content_hash=?", (snapshot_hash,)
            ).fetchone()
            if not row:
                raise CapabilityMappingError(MappingErrorCode.SNAPSHOT_NOT_FOUND)
            cache["snapshot"] = self._stored_capability(row)
        return analysis, finding, event, cache["snapshot"], review_row

    @staticmethod
    def _mapping_schema_available(conn):
        names = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name IN ('capability_mappings', 'capability_mapping_revisions')"
            )
        }
        if len(names) == 1:
            raise CapabilityMappingError(MappingErrorCode.STORAGE_INTEGRITY)
        return bool(names)

    @classmethod
    def _mapping_row(cls, conn, mapping_id):
        if not cls._mapping_schema_available(conn):
            return None
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
        row = (
            conn.execute(
                "SELECT mapping_id FROM capability_mappings "
                "WHERE analysis_id=? AND finding_id=? AND snapshot_hash=?",
                (analysis_id, finding_id, snapshot_hash),
            ).fetchone()
            if self._mapping_schema_available(conn)
            else None
        )
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
            if not self._mapping_schema_available(conn):
                return []
            rows = conn.execute(
                "SELECT m.*, r.revision, r.status, r.review_sequence, r.payload "
                "FROM capability_mappings m JOIN capability_mapping_revisions r USING(mapping_id) "
                "WHERE m.mapping_id=? ORDER BY r.revision",
                (str(mapping_id),),
            ).fetchall()
            return [self._stored_mapping(conn, row) for row in rows]

    @staticmethod
    def _check_skills(snapshot, skills):
        if not set(skills) <= {s.skill_id for s in snapshot.skill_catalog}:
            raise CapabilityMappingError(MappingErrorCode.UNKNOWN_SKILL)

    @staticmethod
    def _insert_mapping_revision(conn, record, review_sequence):
        conn.execute(
            "INSERT INTO capability_mapping_revisions "
            "(mapping_id, revision, status, review_sequence, payload) VALUES (?, ?, ?, ?, ?)",
            (
                str(record.mapping_id),
                record.revision,
                record.status.value,
                review_sequence,
                record.model_dump_json(),
            ),
        )

    def create_capability_mapping_draft(self, request: MappingDraftInput):
        request = self._validate_mapping_input(request, MappingDraftInput)
        with self._mapping_transaction(write=True) as conn:
            _, finding, event, snapshot, review_row = self._mapping_sources(
                conn, request.analysis_id, request.finding_id, request.snapshot_hash
            )
            binding = _binding(finding, event)
            _check_binding(binding, request)
            self._check_skills(snapshot, request.skill_ids)
            exists = conn.execute(
                "SELECT 1 FROM capability_mappings "
                "WHERE analysis_id=? AND finding_id=? AND snapshot_hash=?",
                (request.analysis_id, request.finding_id, request.snapshot_hash),
            ).fetchone()
            if exists:
                raise CapabilityMappingError(MappingErrorCode.ALREADY_EXISTS)
            now = datetime.now(UTC)
            record = CapabilityMappingRecord(
                mapping_id=uuid4(),
                analysis_id=request.analysis_id,
                finding_id=request.finding_id,
                snapshot_hash=request.snapshot_hash,
                operator=request.operator,
                skill_ids=request.skill_ids,
                review_binding=binding,
                revision=1,
                status=MappingLifecycle.DRAFT,
                created_at=now,
                recorded_at=now,
                approved_at=None,
                rationale=request.rationale,
                operator_alias=request.operator_alias,
            )
            conn.execute(
                "INSERT INTO capability_mappings VALUES (?, ?, ?, ?, ?)",
                (
                    str(record.mapping_id),
                    record.analysis_id,
                    record.finding_id,
                    record.snapshot_hash,
                    record.created_at.isoformat(),
                ),
            )
            self._insert_mapping_revision(
                conn, record, review_row["sequence"] if review_row else None
            )
            return record

    def _expected_mapping(self, conn, request):
        row = self._mapping_row(conn, request.mapping_id)
        if row is None:
            raise CapabilityMappingError(MappingErrorCode.NOT_FOUND)
        record = self._stored_mapping(conn, row)
        if record.revision != request.expected_revision:
            raise CapabilityMappingError(MappingErrorCode.CONFLICT)
        return record, row

    @staticmethod
    def _next_record(current, request, status, **changes):
        now = datetime.now(UTC)
        values = current.model_dump()
        values.update(
            revision=current.revision + 1,
            status=status,
            recorded_at=now,
            approved_at=now if status == MappingLifecycle.APPROVED else None,
            rationale=request.rationale,
            operator_alias=request.operator_alias,
            **changes,
        )
        return CapabilityMappingRecord.model_validate(values)

    def revise_capability_mapping(self, request: MappingRevisionInput):
        request = self._validate_mapping_input(request, MappingRevisionInput)
        with self._mapping_transaction(write=True) as conn:
            current, _ = self._expected_mapping(conn, request)
            _, finding, event, snapshot, review_row = self._mapping_sources(
                conn, current.analysis_id, current.finding_id, current.snapshot_hash
            )
            binding = _binding(finding, event)
            _check_binding(binding, request)
            self._check_skills(snapshot, request.skill_ids)
            record = self._next_record(
                current,
                request,
                MappingLifecycle.DRAFT,
                review_binding=binding,
                operator=request.operator,
                skill_ids=request.skill_ids,
            )
            self._insert_mapping_revision(
                conn, record, review_row["sequence"] if review_row else None
            )
            return record

    def approve_capability_mapping(self, request: MappingApprovalInput):
        request = self._validate_mapping_input(request, MappingApprovalInput)
        with self._mapping_transaction(write=True) as conn:
            current, _ = self._expected_mapping(conn, request)
            if current.status != MappingLifecycle.DRAFT:
                raise CapabilityMappingError(MappingErrorCode.INVALID_TRANSITION)
            _, finding, event, snapshot, review_row = self._mapping_sources(
                conn, current.analysis_id, current.finding_id, current.snapshot_hash
            )
            binding = _binding(finding, event)
            _check_binding(binding, request)
            if binding != current.review_binding:
                raise CapabilityMappingError(MappingErrorCode.REVIEW_CONFLICT)
            if not _eligible(finding, event):
                raise CapabilityMappingError(MappingErrorCode.INELIGIBLE)
            self._check_skills(snapshot, current.skill_ids)
            record = self._next_record(current, request, MappingLifecycle.APPROVED)
            self._insert_mapping_revision(conn, record, review_row["sequence"])
            return record

    def revoke_capability_mapping(self, request: MappingRevocationInput):
        request = self._validate_mapping_input(request, MappingRevocationInput)
        with self._mapping_transaction(write=True) as conn:
            current, row = self._expected_mapping(conn, request)
            if current.status == MappingLifecycle.REVOKED:
                raise CapabilityMappingError(MappingErrorCode.INVALID_TRANSITION)
            record = self._next_record(current, request, MappingLifecycle.REVOKED)
            self._insert_mapping_revision(conn, record, row["review_sequence"])
            return record

    def list_capability_mappings(self, *, analysis_id=None, snapshot_hash=None, limit=50):
        if type(limit) is not int or not 1 <= limit <= 100:
            raise CapabilityMappingError(MappingErrorCode.INVALID_INPUT)
        if snapshot_hash is not None:
            self._check_capability_hash(snapshot_hash)
        with self._mapping_transaction() as conn:
            if not self._mapping_schema_available(conn):
                return []
            rows = conn.execute(
                "SELECT analysis_id, finding_id, snapshot_hash FROM capability_mappings "
                "WHERE (? IS NULL OR analysis_id=?) AND (? IS NULL OR snapshot_hash=?) "
                "ORDER BY analysis_id, finding_id, snapshot_hash LIMIT ?",
                (analysis_id, analysis_id, snapshot_hash, snapshot_hash, limit),
            ).fetchall()
            return [
                self._inspection(conn, r["analysis_id"], r["finding_id"], r["snapshot_hash"])
                for r in rows
            ]

    def stale_capability_mappings(self, **filters):
        return [
            m for m in self.list_capability_mappings(**filters) if m.status == MappingStatus.STALE
        ]

    def load_persisted_matching_inputs(self, request):
        """One consistent read snapshot; no approvals can leak across requirement revisions."""
        from tendercite.domain.capability_matching import CapabilityMatchingRequest

        request = self._validate_mapping_input(request, CapabilityMatchingRequest)
        if request.mappings:
            raise CapabilityMappingError(MappingErrorCode.INVALID_INPUT)
        state = self.load_capability_workflow_state(
            request.analysis_id, request.snapshot_hash, finding_ids=request.finding_ids
        )
        return state.analysis, list(state.findings), state.snapshot, state.inspections

    def load_capability_workflow_state(self, analysis_id, snapshot_hash, *, finding_ids=None):
        """Read every selected review, mapping and snapshot on the same connection.

        None selects the analysis inventory; an empty tuple deliberately selects nothing.
        This also verifies analysis/snapshot identity for empty assessments.
        """
        from tendercite.domain.capability_workflow import AssessmentSelection, WorkflowScope

        scope = self._validate_mapping_input(
            WorkflowScope(analysis_id=analysis_id, snapshot_hash=snapshot_hash), WorkflowScope
        )
        with self._mapping_transaction() as conn:
            row = conn.execute(
                "SELECT payload FROM analyses WHERE id=?", (scope.analysis_id,)
            ).fetchone()
            if not row:
                raise CapabilityMappingError(MappingErrorCode.INVALID_SCOPE)
            analysis = AnalysisRun.model_validate_json(row["payload"])
            inventory = tuple(f.id for f in analysis.findings)
            if analysis.id != scope.analysis_id or len(set(inventory)) != len(inventory):
                raise CapabilityMappingError(MappingErrorCode.STORAGE_INTEGRITY)
            selection = AssessmentSelection(
                **scope.model_dump(), finding_ids=inventory if finding_ids is None else finding_ids
            )
            row = conn.execute(
                "SELECT * FROM capability_snapshots WHERE content_hash=?", (scope.snapshot_hash,)
            ).fetchone()
            if not row:
                raise CapabilityMappingError(MappingErrorCode.SNAPSHOT_NOT_FOUND)
            snapshot = self._stored_capability(row)
            cache = {"analysis": analysis, "snapshot": snapshot}
            findings, inspections, bindings = [], [], []
            for finding_id in selection.finding_ids:
                sources = self._mapping_sources(
                    conn, scope.analysis_id, finding_id, scope.snapshot_hash, cache=cache
                )
                _, finding, event, _, _ = sources
                findings.append(finding)
                bindings.append(_binding(finding, event))
                inspections.append(
                    self._inspection(
                        conn, scope.analysis_id, finding_id, scope.snapshot_hash, sources
                    )
                )
            return CapabilityWorkflowState(
                analysis, snapshot, tuple(findings), tuple(inspections), tuple(bindings)
            )

    def list_analysis_metadata(self, *, limit=50):
        """Bounded local selection metadata; never export queries or requirement text."""
        from tendercite.domain.capability_workflow import AnalysisInventory, AnalysisMetadata

        if type(limit) is not int or not 1 <= limit <= 100:
            raise CapabilityMappingError(MappingErrorCode.INVALID_INPUT)
        with self._mapping_transaction() as conn:
            rows = conn.execute("SELECT id, payload FROM analyses ORDER BY id LIMIT ?", (limit,))
            metadata = []
            for row in rows:
                analysis = AnalysisRun.model_validate_json(row["payload"])
                if analysis.id != row["id"]:
                    raise CapabilityMappingError(MappingErrorCode.STORAGE_INTEGRITY)
                metadata.append(
                    AnalysisMetadata(
                        analysis_id=analysis.id,
                        created_at=analysis.created_at,
                        finding_count=len(analysis.findings),
                    )
                )
            return AnalysisInventory(analyses=tuple(metadata))

    def usable_capability_mappings(self, request):
        from tendercite.domain.capability_matching import CapabilityMatchingRequest

        request = self._validate_mapping_input(request, CapabilityMatchingRequest)
        if request.mappings:
            raise CapabilityMappingError(MappingErrorCode.INVALID_INPUT)
        _, _, _, inspections = self.load_persisted_matching_inputs(request)
        return tuple(m.record for m in inspections if m.status == MappingStatus.APPROVED)
