"""Bounded, explicit daily OCDS downloads; no website/private API or remote keyword search."""

import hashlib
import io
import json
import re
import unicodedata
import zipfile
from datetime import UTC, date, datetime

import httpx

from tendercite.domain.bkms import (
    MAX_RECORDS,
    BkmsError,
    CatalogContents,
    LoadInput,
    SearchInput,
    SearchResult,
)
from tendercite.domain.bkms_reference import (
    LICENSE,
    ORIGIN,
    POLICY,
    BkmsSource,
    OriginalMetadata,
    notice_parts,
    notice_url,
    validate_record_url,
)

EXPORT_URL = ORIGIN + "/api/notice-exports"
MAX_DOWNLOAD_BYTES = 16 * 1024 * 1024
MAX_EXPANDED_BYTES = 64 * 1024 * 1024
MAX_RECORD_BYTES = 2 * 1024 * 1024


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate object key")
        result[key] = value
    return result


def _constant(_):
    raise ValueError("Non-finite JSON value")


def _record(raw: bytes, identity: str, day: date, archive_hash: str, retrieved: datetime):
    notice_id, _ = notice_parts(identity)
    body = json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs, parse_constant=_constant)
    if body.get("license") != LICENSE or body.get("publicationPolicy") != POLICY:
        raise BkmsError("BKMS_UNSUPPORTED_LICENSE")
    releases = body.get("releases")
    if body.get("version") != "1.1" or not isinstance(releases, list) or len(releases) != 1:
        raise ValueError("Unsupported release package")
    release = releases[0]
    if release.get("id") != notice_id:
        raise ValueError("Mismatched notice identity")
    record_url = validate_record_url(body["uri"], identity)
    tender = release.get("tender", {})
    buyer = release.get("buyer", {})
    period = tender.get("tenderPeriod", {})
    tags = release.get("tag", [])
    if not isinstance(tags, list):
        raise ValueError("Unsupported tags")
    warnings = []

    def source_text(value, size):
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValueError("Unsupported field shape")
        if len(value) > size and "SOURCE_METADATA_SHORTENED" not in warnings:
            warnings.append("SOURCE_METADATA_SHORTENED")
        return value[:size]

    title, organization, description = (
        tender.get("title"),
        buyer.get("name"),
        tender.get("description"),
    )
    for value, size, missing, shortened in (
        (title, 200, "MISSING_TITLE", "TITLE_SHORTENED"),
        (organization, 200, "MISSING_BUYER", "ORGANIZATION_SHORTENED"),
        (description, 2000, "MISSING_DESCRIPTION", "SUMMARY_SHORTENED"),
    ):
        if value is not None and not isinstance(value, str):
            raise ValueError("Unsupported field shape")
        if not value or not value.strip():
            warnings.append(missing)
        elif len(value.strip()) > size:
            warnings.append(shortened)
    meta = OriginalMetadata(
        title=source_text(title, 1000),
        buyer=source_text(organization, 500),
        description=source_text(description, 8000),
        published_at=body.get("publishedDate"),
        deadline=period.get("endDate"),
        tags=tuple(tags),
    )
    if meta.published_at is None:
        warnings.append("MISSING_PUBLICATION_DATE")
    elif date.fromisoformat(meta.published_at[:10]) != day:
        raise ValueError("Publication outside selected day")
    if meta.deadline is None:
        warnings.append("MISSING_DEADLINE")
    return BkmsSource(
        external_id=identity,
        original_url=notice_url(identity),
        record_url=record_url,
        ocid=release.get("ocid"),
        export_day=day,
        retrieved_at=retrieved,
        archive_sha256=archive_hash,
        record_sha256=hashlib.sha256(raw).hexdigest(),
        original_metadata=meta,
        mapping_warnings=tuple(warnings),
    )


def parse_archive(
    raw: bytes, day: date, *, retrieved_at: datetime | None = None
) -> CatalogContents:
    if len(raw) > MAX_DOWNLOAD_BYTES:
        raise BkmsError("BKMS_EXPORT_TOO_LARGE")
    archive_hash = hashlib.sha256(raw).hexdigest()
    retrieved_at = retrieved_at or datetime.now(UTC)
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            files = archive.infolist()
            if len(files) > MAX_RECORDS or sum(f.file_size for f in files) > MAX_EXPANDED_BYTES:
                raise BkmsError("BKMS_EXPORT_TOO_LARGE")
            records = []
            for info in files:
                if (
                    not re.fullmatch(
                        r"(?:[0-9a-f-]{36}|[0-9]{1,20})-[0-9]{1,10}\.json", info.filename
                    )
                    or info.flag_bits & 1
                    or info.is_dir()
                    or info.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED)
                    or (info.external_attr >> 16) & 0o170000 == 0o120000
                ):
                    raise ValueError("Unsupported archive entry")
                if info.file_size > MAX_RECORD_BYTES:
                    raise BkmsError("BKMS_EXPORT_TOO_LARGE")
                with archive.open(info) as stream:
                    value = stream.read(MAX_RECORD_BYTES + 1)
                if len(value) > MAX_RECORD_BYTES:
                    raise BkmsError("BKMS_EXPORT_TOO_LARGE")
                records.append(_record(value, info.filename[:-5], day, archive_hash, retrieved_at))
        return CatalogContents(
            export_day=day,
            archive_sha256=archive_hash,
            retrieved_at=retrieved_at,
            records=tuple(sorted(records, key=lambda r: r.external_id)),
        )
    except BkmsError:
        raise
    except (
        ValueError,
        KeyError,
        TypeError,
        AttributeError,
        UnicodeError,
        RecursionError,
        OverflowError,
        zipfile.BadZipFile,
        RuntimeError,
        NotImplementedError,
    ):
        raise BkmsError("BKMS_INVALID_EXPORT") from None


class BkmsClient:
    def __init__(self, client: httpx.Client | None = None):
        self._owned = client is None
        self.client = client or httpx.Client(
            timeout=httpx.Timeout(30, connect=10), follow_redirects=False
        )

    def __enter__(self):
        return self

    def __exit__(self, *args):
        if self._owned:
            self.client.close()

    def load(self, request: LoadInput) -> CatalogContents:
        try:
            with self.client.stream(
                "GET",
                EXPORT_URL,
                params={"pubDay": request.export_day.isoformat(), "format": "ocds.zip"},
                headers={"Accept-Encoding": "identity"},
                follow_redirects=False,
            ) as response:
                if response.status_code == 429:
                    raise BkmsError("BKMS_RATE_LIMITED")
                if response.status_code != 200:
                    raise BkmsError("BKMS_UNAVAILABLE")
                chunks, size = [], 0
                for chunk in response.iter_bytes(chunk_size=65536):
                    size += len(chunk)
                    if size > MAX_DOWNLOAD_BYTES:
                        raise BkmsError("BKMS_EXPORT_TOO_LARGE")
                    chunks.append(chunk)
            return parse_archive(b"".join(chunks), request.export_day)
        except httpx.TimeoutException:
            raise BkmsError("BKMS_TIMEOUT") from None
        except httpx.RequestError:
            raise BkmsError("BKMS_UNAVAILABLE") from None


def search_catalog(repository, request: SearchInput) -> SearchResult:
    catalog, summary = repository.get_bkms_catalog(request.catalog_id)

    def normalized(value):
        return " ".join(unicodedata.normalize("NFKC", value).casefold().split())

    keyword = normalized(request.keyword)
    records = [
        r
        for r in catalog.records
        if any(
            keyword in normalized(value)
            for value in (
                r.original_metadata.title,
                r.original_metadata.buyer,
                r.original_metadata.description,
            )
            if value is not None
        )
    ]
    start = (request.page - 1) * request.limit
    return SearchResult(
        catalog=summary,
        keyword=request.keyword,
        page=request.page,
        limit=request.limit,
        total_count=len(records),
        items=tuple(records[start : start + request.limit]),
        next_page=request.page + 1 if start + request.limit < len(records) else None,
    )
