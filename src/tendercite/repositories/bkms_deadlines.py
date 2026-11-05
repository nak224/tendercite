"""Digest-bound, replaceable metadata for retained days; never edits native opportunities."""

import hashlib
from collections import Counter

from tendercite.domain.bkms import BkmsError
from tendercite.domain.bkms_deadlines import (
    MAX_DEADLINE_BYTES,
    DeadlineCatalog,
    DeadlineEvidence,
    DeadlineLoadInput,
    DeadlineSummary,
)
from tendercite.repositories.bkms import _migrate_legacy, _tables

DEADLINE_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS bkms_deadline_days (
    export_day TEXT PRIMARY KEY NOT NULL REFERENCES bkms_catalog_days(export_day) ON DELETE CASCADE,
    content_hash TEXT NOT NULL CHECK(length(content_hash)=64),
    payload TEXT NOT NULL CHECK(length(CAST(payload AS BLOB)) BETWEEN 1 AND 8388608)
);
"""


def _stored(conn, days=None):
    if "bkms_deadline_days" not in _tables(conn):
        return {}
    size = conn.execute(
        "SELECT count(*),COALESCE(sum(length(CAST(payload AS BLOB))),0) FROM bkms_deadline_days"
    ).fetchone()
    if size[0] > 7 or size[1] > 7 * MAX_DEADLINE_BYTES:
        raise BkmsError("BKMS_STORAGE_INTEGRITY")
    entries = {}
    query = "SELECT * FROM bkms_deadline_days"
    if days is not None:
        query += " WHERE export_day IN (" + ",".join("?" for _ in days) + ")"
    for row in conn.execute(query, () if days is None else days):
        try:
            raw = row["payload"].encode("utf-8")
            catalog = DeadlineCatalog.model_validate_json(raw)
            if (
                len(raw) > MAX_DEADLINE_BYTES
                or catalog.export_day.isoformat() != row["export_day"]
                or hashlib.sha256(raw).hexdigest() != row["content_hash"]
            ):
                raise ValueError("Invalid stored metadata")
        except (ValueError, TypeError, AttributeError):
            raise BkmsError("BKMS_STORAGE_INTEGRITY") from None
        entries[catalog.export_day] = (catalog, row["content_hash"])
    return entries


def _summary(summary, entry):
    if entry is None:
        return DeadlineSummary(
            state="NOT_LOADED",
            catalog_id=summary.catalog_id,
            export_day=summary.export_day,
            counts={},
        )
    data, digest = entry
    return DeadlineSummary(
        state="CURRENT" if data.catalog_id == summary.catalog_id else "STALE",
        catalog_id=summary.catalog_id,
        export_day=summary.export_day,
        enrichment_id=digest,
        bound_catalog_id=data.catalog_id,
        archive_sha256=data.archive_sha256,
        retrieved_at=data.retrieved_at,
        counts=dict(Counter(r.status for r in data.records)),
    )


def _expected(entry, expected):
    if (entry[1] if entry else None) != expected:
        raise BkmsError("BKMS_DEADLINE_CONFLICT")


class BkmsDeadlineRepositoryMixin:
    def get_bkms_deadline_summary(self, catalog_id):
        if not self.database_path.exists():
            raise BkmsError("BKMS_CATALOG_NOT_FOUND")
        with self._opportunity_transaction() as conn:
            _, summary = self._bkms_catalog(conn, catalog_id)
            return _summary(summary, _stored(conn).get(summary.export_day))

    def check_bkms_deadline_load(self, request: DeadlineLoadInput):
        with self._opportunity_transaction() as conn:
            _, summary = self._bkms_catalog(conn, request.catalog_id)
            _expected(_stored(conn).get(summary.export_day), request.expected_enrichment_id)
            return summary

    def save_bkms_deadlines(self, data: DeadlineCatalog, request: DeadlineLoadInput):
        data = DeadlineCatalog.model_validate(data)
        request = DeadlineLoadInput.model_validate(request)
        raw = data.model_dump_json()
        if len(raw.encode()) > MAX_DEADLINE_BYTES:
            raise BkmsError("BKMS_EXPORT_TOO_LARGE")
        digest = hashlib.sha256(raw.encode()).hexdigest()
        with self._opportunity_transaction(write=True) as conn:
            _migrate_legacy(conn)
            conn.execute(DEADLINE_SCHEMA_SQL)
            catalog, summary = self._bkms_catalog(conn, request.catalog_id)
            _expected(_stored(conn).get(summary.export_day), request.expected_enrichment_id)
            if (
                data.catalog_id != request.catalog_id
                or data.export_day != catalog.export_day
                or {r.external_id for r in data.records} != {r.external_id for r in catalog.records}
            ):
                raise BkmsError("BKMS_EFORMS_IDENTITY_MISMATCH")
            conn.execute(
                "INSERT INTO bkms_deadline_days VALUES (?,?,?) ON CONFLICT(export_day) "
                "DO UPDATE SET content_hash=excluded.content_hash,payload=excluded.payload",
                (data.export_day.isoformat(), digest, raw),
            )
            return _summary(summary, (data, digest))

    def get_bkms_deadline_evidence(self, coverage, selected=None):
        """Only the exact selected daily snapshot contributes; no fallback across days/versions."""
        if not self.database_path.exists():
            return {}
        wanted = None if selected is None else {(s.export_day, s.external_id) for s in selected}
        days = None if wanted is None else tuple(sorted({day.isoformat() for day, _ in wanted}))
        if days == ():
            return {}
        with self._opportunity_transaction() as conn:
            entries = _stored(conn, days)
        result = {}
        for day in coverage.days:
            entry = entries.get(day.catalog.export_day)
            if entry is None or entry[0].catalog_id != day.catalog.catalog_id:
                continue
            data, digest = entry
            result[data.catalog_id] = {
                r.external_id: DeadlineEvidence(
                    enrichment_id=digest,
                    catalog_id=data.catalog_id,
                    export_day=data.export_day,
                    archive_sha256=data.archive_sha256,
                    retrieved_at=data.retrieved_at,
                    notice=r,
                )
                for r in data.records
                if wanted is None or (data.export_day, r.external_id) in wanted
            }
        return result
