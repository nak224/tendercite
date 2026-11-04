"""One replaceable daily catalog and explicit imports into the existing native opportunity table."""

import hashlib

from tendercite.domain.bkms import (
    MAX_CATALOG_BYTES,
    BkmsError,
    CatalogContents,
    CatalogSummary,
    ImportInput,
    mapped_text,
    native_opportunity,
)
from tendercite.domain.bkms_reference import opportunity_id
from tendercite.domain.opportunities import (
    OpportunityError,
    OpportunityErrorCode,
    OpportunitySaveResult,
)

BKMS_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS bkms_catalog (
    singleton INTEGER PRIMARY KEY CHECK(singleton=1),
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


class BkmsRepositoryMixin:
    @staticmethod
    def _bkms_catalog(conn, expected=None):
        if not conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='bkms_catalog'"
        ).fetchone():
            raise BkmsError("BKMS_CATALOG_NOT_FOUND")
        row = conn.execute("SELECT * FROM bkms_catalog WHERE singleton=1").fetchone()
        if row is None:
            raise BkmsError("BKMS_CATALOG_NOT_FOUND")
        try:
            raw = row["payload"].encode("utf-8")
            if (
                len(raw) > MAX_CATALOG_BYTES
                or hashlib.sha256(raw).hexdigest() != row["content_hash"]
            ):
                raise ValueError("Invalid catalog digest")
            catalog = CatalogContents.model_validate_json(raw)
        except (ValueError, TypeError, AttributeError):
            raise BkmsError("BKMS_STORAGE_INTEGRITY") from None
        if expected is not None and expected != row["content_hash"]:
            raise BkmsError("BKMS_CATALOG_CONFLICT")
        return catalog, _summary(catalog, row["content_hash"])

    def get_bkms_catalog(self, expected=None):
        if not self.database_path.exists():
            raise BkmsError("BKMS_CATALOG_NOT_FOUND")
        with self._opportunity_transaction() as conn:
            return self._bkms_catalog(conn, expected)

    def save_bkms_catalog(self, catalog: CatalogContents) -> CatalogSummary:
        catalog = CatalogContents.model_validate(catalog)
        payload = catalog.model_dump_json()
        raw = payload.encode("utf-8")
        if len(raw) > MAX_CATALOG_BYTES:
            raise BkmsError("BKMS_EXPORT_TOO_LARGE")
        content_hash = hashlib.sha256(raw).hexdigest()
        with self._opportunity_transaction(write=True) as conn:
            # Replacement is atomic; a failed download/parse never reaches this operation.
            conn.execute(
                "INSERT INTO bkms_catalog VALUES (1, ?, ?) ON CONFLICT(singleton) DO UPDATE SET "
                "content_hash=excluded.content_hash, payload=excluded.payload",
                (content_hash, payload),
            )
        return _summary(catalog, content_hash)

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
                # An already imported publication remains usable after catalog replacement.
                return OpportunitySaveResult(status="ALREADY_PRESENT", record=current)
            catalog, _ = self._bkms_catalog(conn, request.catalog_id)
            source = next(
                (r for r in catalog.records if r.external_id == request.external_id), None
            )
            if source is None:
                raise BkmsError("BKMS_NOTICE_NOT_FOUND")
            if mapped_text(source.original_metadata.title, 200) is None:
                raise BkmsError("BKMS_NOT_IMPORTABLE")
            return self._insert_opportunity(conn, identity, native_opportunity(source))
