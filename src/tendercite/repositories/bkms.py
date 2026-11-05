"""Seven retained daily snapshots; transactional lazy migration and unchanged native imports."""

import hashlib
import json
from collections import Counter

from tendercite.domain.bkms import (
    MAX_CATALOG_BYTES,
    BkmsError,
    CatalogContents,
    CatalogSummary,
    ImportInput,
    mapped_text,
    native_opportunity,
)
from tendercite.domain.bkms_catalogs import (
    MAX_DAYS,
    MAX_TOTAL_BYTES,
    CatalogCollection,
    RemoveInput,
    StoredDay,
)
from tendercite.domain.bkms_reference import opportunity_id
from tendercite.domain.opportunities import (
    OpportunityError,
    OpportunityErrorCode,
    OpportunitySaveResult,
)

BKMS_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS bkms_catalog_days (
    export_day TEXT PRIMARY KEY NOT NULL CHECK(length(export_day)=10),
    content_hash TEXT NOT NULL CHECK(length(content_hash)=64),
    payload TEXT NOT NULL CHECK(length(CAST(payload AS BLOB)) BETWEEN 1 AND 50331648)
);
"""


def _summary(catalog, content_hash):
    return CatalogSummary(
        catalog_id=content_hash,
        export_day=catalog.export_day,
        archive_sha256=catalog.archive_sha256,
        retrieved_at=catalog.retrieved_at,
        record_count=len(catalog.records),
        importable_count=sum(
            bool(mapped_text(r.original_metadata.title, 200)) for r in catalog.records
        ),
        warning_count=sum(bool(r.mapping_warnings) for r in catalog.records),
    )


def _tables(conn):
    return {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _decode(row, *, daily=False):
    try:
        raw = row["payload"].encode("utf-8")
        if len(raw) > MAX_CATALOG_BYTES or hashlib.sha256(raw).hexdigest() != row["content_hash"]:
            raise ValueError("Invalid catalog digest")
        catalog = CatalogContents.model_validate_json(raw)
        if daily and catalog.export_day.isoformat() != row["export_day"]:
            raise ValueError("Invalid catalog day")
    except (ValueError, TypeError, AttributeError):
        raise BkmsError("BKMS_STORAGE_INTEGRITY") from None
    return catalog, _summary(catalog, row["content_hash"]), len(raw)


def _catalogs(conn):
    tables = _tables(conn)
    names = [n for n in ("bkms_catalog_days", "bkms_catalog") if n in tables]
    # Check aggregate bytes before reading/decoding payloads. The legacy backup is inactive.
    sizes = [
        conn.execute(
            f"SELECT COUNT(*), COALESCE(SUM(length(CAST(payload AS BLOB))),0) FROM {name}"
        ).fetchone()
        for name in names
    ]
    if sum(r[0] for r in sizes) > MAX_DAYS + 1 or sum(r[1] for r in sizes) > MAX_TOTAL_BYTES:
        raise BkmsError("BKMS_STORAGE_INTEGRITY")
    by_day = {}
    for name in names:
        for row in conn.execute(f"SELECT * FROM {name}"):
            entry = _decode(row, daily=name == "bkms_catalog_days")
            day = entry[0].export_day
            if day in by_day and by_day[day][1].catalog_id != entry[1].catalog_id:
                raise BkmsError("BKMS_STORAGE_INTEGRITY")
            by_day[day] = entry
    if len(by_day) > MAX_DAYS:
        raise BkmsError("BKMS_STORAGE_INTEGRITY")
    return [by_day[day] for day in sorted(by_day, reverse=True)]


def _collection(entries):
    scope = [[summary.export_day.isoformat(), summary.catalog_id] for _, summary, _ in entries]
    digest = hashlib.sha256(json.dumps(scope, separators=(",", ":")).encode()).hexdigest()
    return CatalogCollection(
        collection_id=digest,
        stored_bytes=sum(size for _, _, size in entries),
        days=tuple(
            StoredDay(
                catalog=summary,
                warning_counts=dict(
                    Counter(w for r in catalog.records for w in r.mapping_warnings)
                ),
            )
            for catalog, summary, _ in entries
        ),
    )


def _migrate_legacy(conn):
    conn.execute(BKMS_SCHEMA_SQL)
    if "bkms_catalog" not in _tables(conn):
        return
    if "bkms_catalog_legacy" in _tables(conn):
        raise BkmsError("BKMS_STORAGE_INTEGRITY")
    _catalogs(conn)  # Validate conflicts/integrity before copying anything.
    row = conn.execute("SELECT * FROM bkms_catalog WHERE singleton=1").fetchone()
    if row is not None:
        catalog, _, _ = _decode(row)
        conn.execute(
            "INSERT INTO bkms_catalog_days VALUES (?, ?, ?) ON CONFLICT(export_day) DO NOTHING",
            (catalog.export_day.isoformat(), row["content_hash"], row["payload"]),
        )
    # Retain the original table and exact bytes as a recovery copy. Its new name is also
    # the migration marker: explicit day removal must never resurrect the singleton.
    conn.execute("ALTER TABLE bkms_catalog RENAME TO bkms_catalog_legacy")


def _load_state(conn, day, expected):
    entries = _catalogs(conn)
    current = next((entry for entry in entries if entry[0].export_day == day), None)
    if current:
        if expected is None:
            raise BkmsError("BKMS_DAY_ALREADY_STORED")
        if current[1].catalog_id != expected:
            raise BkmsError("BKMS_CATALOG_CONFLICT")
    elif expected is not None:
        raise BkmsError("BKMS_CATALOG_CONFLICT")
    elif len(entries) >= MAX_DAYS:
        raise BkmsError("BKMS_DAY_LIMIT_REACHED")
    return entries, current


class BkmsRepositoryMixin:
    @staticmethod
    def _bkms_catalog(conn, expected=None):
        entries = _catalogs(conn)
        if not entries:
            raise BkmsError("BKMS_CATALOG_NOT_FOUND")
        entry = (
            entries[0]
            if expected is None
            else next((e for e in entries if e[1].catalog_id == expected), None)
        )
        if entry is None:
            raise BkmsError("BKMS_CATALOG_CONFLICT")
        return entry[0], entry[1]

    def get_bkms_catalog(self, expected=None):
        if not self.database_path.exists():
            raise BkmsError("BKMS_CATALOG_NOT_FOUND")
        with self._opportunity_transaction() as conn:
            return self._bkms_catalog(conn, expected)

    def get_bkms_catalogs(self, expected_collection=None):
        if not self.database_path.exists():
            entries = []
        else:
            with self._opportunity_transaction() as conn:
                entries = _catalogs(conn)
        coverage = _collection(entries)
        if expected_collection is not None and coverage.collection_id != expected_collection:
            raise BkmsError("BKMS_CATALOG_CONFLICT")
        return tuple(entry[0] for entry in entries), coverage

    def check_bkms_load(self, request):
        with self._opportunity_transaction() as conn:
            _load_state(conn, request.export_day, request.expected_catalog_id)

    def save_bkms_catalog(self, catalog: CatalogContents, *, expected_catalog_id=None):
        catalog = CatalogContents.model_validate(catalog)
        payload = catalog.model_dump_json()
        raw = payload.encode("utf-8")
        if len(raw) > MAX_CATALOG_BYTES:
            raise BkmsError("BKMS_EXPORT_TOO_LARGE")
        content_hash = hashlib.sha256(raw).hexdigest()
        with self._opportunity_transaction(write=True) as conn:
            _migrate_legacy(conn)
            entries, current = _load_state(conn, catalog.export_day, expected_catalog_id)
            if (
                sum(size for _, _, size in entries) - (current[2] if current else 0) + len(raw)
                > MAX_TOTAL_BYTES
            ):
                raise BkmsError("BKMS_TOTAL_STORAGE_LIMIT")
            if current:
                conn.execute(
                    "UPDATE bkms_catalog_days SET content_hash=?, payload=? "
                    "WHERE export_day=? AND content_hash=?",
                    (content_hash, payload, catalog.export_day.isoformat(), expected_catalog_id),
                )
            else:
                conn.execute(
                    "INSERT INTO bkms_catalog_days VALUES (?, ?, ?)",
                    (catalog.export_day.isoformat(), content_hash, payload),
                )
        return _summary(catalog, content_hash)

    def remove_bkms_catalog(self, request: RemoveInput):
        request = RemoveInput.model_validate(request)
        with self._opportunity_transaction(write=True) as conn:
            _migrate_legacy(conn)
            entries = _catalogs(conn)
            current = next((e for e in entries if e[0].export_day == request.export_day), None)
            if current is None:
                raise BkmsError("BKMS_CATALOG_NOT_FOUND")
            if current[1].catalog_id != request.expected_catalog_id:
                raise BkmsError("BKMS_CATALOG_CONFLICT")
            conn.execute(
                "DELETE FROM bkms_catalog_days WHERE export_day=? AND content_hash=?",
                (request.export_day.isoformat(), request.expected_catalog_id),
            )
            return _collection(_catalogs(conn))

    def import_bkms_opportunity(self, request: ImportInput) -> OpportunitySaveResult:
        request = ImportInput.model_validate(request)
        identity = opportunity_id(request.external_id)
        with self._opportunity_transaction(write=True) as conn:
            row = conn.execute(
                "SELECT * FROM native_opportunities WHERE opportunity_id=?", (str(identity),)
            ).fetchone()
            if row is not None:
                current = self._stored_opportunity(row)
                source = current.opportunity.external_source
                if (
                    current.opportunity.source_origin != "bkms_import"
                    or source is None
                    or source.external_id != request.external_id
                ):
                    raise OpportunityError(OpportunityErrorCode.ID_CONFLICT)
                return OpportunitySaveResult(status="ALREADY_PRESENT", record=current)
            _migrate_legacy(conn)
            catalog, _ = self._bkms_catalog(conn, request.catalog_id)
            source = next(
                (r for r in catalog.records if r.external_id == request.external_id), None
            )
            if source is None:
                raise BkmsError("BKMS_NOTICE_NOT_FOUND")
            if mapped_text(source.original_metadata.title, 200) is None:
                raise BkmsError("BKMS_NOT_IMPORTABLE")
            return self._insert_opportunity(conn, identity, native_opportunity(source))
