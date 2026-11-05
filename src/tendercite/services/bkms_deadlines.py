"""Verified BT-131(d/t)-Lot paths only; no schema fetching or general XML interpretation."""

import hashlib
import io
import re
import zipfile
from datetime import UTC, date, datetime, time
from xml.etree import ElementTree as ET

from tendercite.domain.bkms import BkmsError
from tendercite.domain.bkms_deadlines import (
    MAX_DEADLINE_BYTES,
    DeadlineCatalog,
    DeadlineNotice,
    LotDeadline,
    SourcePeriod,
)
from tendercite.domain.bkms_reference import notice_parts
from tendercite.services.bkms import (
    MAX_DOWNLOAD_BYTES,
    MAX_EXPANDED_BYTES,
    MAX_RECORD_BYTES,
    MAX_RECORDS,
)

NS = {
    "cbc": "urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2",
    "cac": "urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2",
}
ROOTS = {
    f"{{urn:oasis:names:specification:ubl:schema:xsd:{kind}-2}}{kind}"
    for kind in ("ContractNotice", "ContractAwardNotice", "PriorInformationNotice")
}
PERIOD = f"{{{NS['cac']}}}TenderSubmissionDeadlinePeriod"
OFFSET = r"(Z|[+-](?:(?:0[0-9]|1[0-3]):[0-5][0-9]|14:00))?"


def _xml(raw):
    text = raw.decode("utf-8-sig")
    if (
        "\x00" in text
        or re.search(r"<!DOCTYPE|<!ENTITY|<\?(?!xml\s)", text)
        or re.search(r"encoding\s*=\s*['\"](?!UTF-8['\"])", text, re.I)
    ):
        raise ValueError("Unsafe or unsupported XML")
    parser = ET.XMLPullParser(events=("start", "end"))
    depth = count = 0
    root = None
    # Drain bounded chunks while parsing: reject excessive depth/nodes before building a full tree.
    for start in range(0, len(text), 8192):
        parser.feed(text[start : start + 8192])
        for event, element in parser.read_events():
            if event == "start":
                root = element if root is None else root
                count += 1
                depth += 1
                if (
                    depth > 64
                    or count > 30000
                    or element.tag.startswith("{http://www.w3.org/2001/XInclude}")
                ):
                    raise ValueError("Unsafe XML structure")
            else:
                depth -= 1
    parser.close()
    if root is None or root.tag not in ROOTS:
        raise ValueError("Unsupported XML root")
    return root


def _one(root, path):
    fields = root.findall(path, NS)
    if len(fields) != 1 or len(fields[0]) or fields[0].text is None:
        raise ValueError("Missing or repeated identity")
    return fields[0]


def _period_state(periods, warnings):
    if not periods or all(not p.dates and not p.times for p in periods):
        warnings.add("MISSING_SUBMISSION_DEADLINE")
        return "MISSING"
    if len(periods) != 1 or any(len(p.dates) != 1 or len(p.times) > 1 for p in periods):
        warnings.add("CONFLICTING_VALUES")
        return "CONFLICT"
    period = periods[0]
    d = re.fullmatch(r"([0-9]{4}-[0-9]{2}-[0-9]{2})" + OFFSET, period.dates[0].strip())
    t = (
        re.fullmatch(
            r"([0-9]{2}:[0-9]{2}(?::[0-9]{2}(?:\.[0-9]{1,6})?)?)" + OFFSET, period.times[0].strip()
        )
        if period.times
        else None
    )
    try:
        if d is None or (period.times and t is None):
            raise ValueError("Unrecognized source values")
        date.fromisoformat(d[1])
        if t:
            time.fromisoformat(t[1])
    except ValueError:
        warnings.add("UNRECOGNIZED_STRUCTURE")
        return "UNRECOGNIZED"
    if not period.times:
        warnings.add("MISSING_TIME")
    if not d[2] or (t and not t[2]):
        warnings.add("MISSING_TIMEZONE")

    def offset(v):
        return "+00:00" if v in ("Z", "-00:00") else v

    if t and d[2] and t[2] and offset(d[2]) != offset(t[2]):
        warnings.add("OFFSET_CONFLICT")
        return "CONFLICT"
    return "SINGLE"


def _record(raw, identity):
    notice_id, version = notice_parts(identity)
    root = _xml(raw)
    id_field = _one(root, "cbc:ID")
    customization = _one(root, "cbc:CustomizationID").text
    if id_field.text != notice_id or _one(root, "cbc:VersionID").text != version:
        raise ValueError("Mismatched source identity")
    common = dict(
        external_id=identity,
        record_sha256=hashlib.sha256(raw).hexdigest(),
        customization_id=customization,
    )
    if customization != "eforms-de-2.1":
        return DeadlineNotice(
            **common, status="UNRECOGNIZED", lots=(), warnings=("UNSUPPORTED_CUSTOMIZATION",)
        )
    if id_field.attrib.get("schemeName") != "notice-id":
        raise ValueError("Invalid supported source identity")
    warnings, lots, used = set(), [], set()
    recognized_periods = 0
    for lot in root.findall("cac:ProcurementProjectLot", NS):
        if len(lots) >= 128:
            raise ValueError("Lot budget exceeded")
        lot_id = _one(lot, "cbc:ID")
        if lot_id.attrib.get("schemeName") != "Lot" or not re.fullmatch(
            r"LOT-[0-9]{4}", lot_id.text
        ):
            warnings.add("UNRECOGNIZED_STRUCTURE")
            continue
        nodes = lot.findall("cac:TenderingProcess/cac:TenderSubmissionDeadlinePeriod", NS)
        recognized_periods += len(nodes)
        periods = []
        for node in nodes:
            # Only scalar fields; do not infer values from descendants or extension paths.
            if any(
                len(c) or c.tag not in (f"{{{NS['cbc']}}}EndDate", f"{{{NS['cbc']}}}EndTime")
                for c in node
            ):
                warnings.add("UNRECOGNIZED_STRUCTURE")
            periods.append(
                SourcePeriod(
                    dates=tuple(e.text or "" for e in node.findall("cbc:EndDate", NS)),
                    times=tuple(e.text or "" for e in node.findall("cbc:EndTime", NS)),
                )
            )
        state = _period_state(periods, warnings)
        if lot_id.text in used:
            warnings.add("DUPLICATE_LOT")
            state = "CONFLICT"
        used.add(lot_id.text)
        lots.append(LotDeadline(lot_id=lot_id.text, periods=tuple(periods), status=state))
    # A deadline at an undocumented path/namespace is never silently treated as absent.
    if (
        sum(e.tag.rsplit("}", 1)[-1] == "TenderSubmissionDeadlinePeriod" for e in root.iter())
        != recognized_periods
    ):
        warnings.add("UNRECOGNIZED_STRUCTURE")
    states = {lot.status for lot in lots}
    if "CONFLICT" in states:
        status = "CONFLICT"
    elif "UNRECOGNIZED" in states or "UNRECOGNIZED_STRUCTURE" in warnings:
        status = "UNRECOGNIZED"
    elif not states or states == {"MISSING"}:
        status = "MISSING"
        warnings.add("MISSING_SUBMISSION_DEADLINE")
    else:
        status = "MULTIPLE_LOTS" if len(lots) > 1 else "SINGLE"
    return DeadlineNotice(
        **common, status=status, lots=tuple(lots), warnings=tuple(sorted(warnings))
    )


def parse_eforms_archive(raw, day, catalog_id, *, retrieved_at=None):
    if len(raw) > MAX_DOWNLOAD_BYTES:
        raise BkmsError("BKMS_EXPORT_TOO_LARGE")
    try:
        records = []
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            files = archive.infolist()
            if len(files) > MAX_RECORDS or sum(f.file_size for f in files) > MAX_EXPANDED_BYTES:
                raise BkmsError("BKMS_EXPORT_TOO_LARGE")
            for info in files:
                if (
                    not re.fullmatch(
                        r"(?:[0-9a-f-]{36}|[0-9]{1,20})-[0-9]{1,10}\.xml", info.filename
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
                records.append(_record(value, info.filename[:-4]))
        catalog = DeadlineCatalog(
            catalog_id=catalog_id,
            export_day=day,
            archive_sha256=hashlib.sha256(raw).hexdigest(),
            retrieved_at=retrieved_at or datetime.now(UTC),
            records=tuple(sorted(records, key=lambda r: r.external_id)),
        )
        if len(catalog.model_dump_json().encode()) > MAX_DEADLINE_BYTES:
            raise BkmsError("BKMS_EXPORT_TOO_LARGE")
        return catalog
    except BkmsError:
        raise
    except (
        ValueError,
        TypeError,
        AttributeError,
        UnicodeError,
        ET.ParseError,
        RuntimeError,
        zipfile.BadZipFile,
        NotImplementedError,
        RecursionError,
        OverflowError,
    ):
        raise BkmsError("BKMS_INVALID_EFORMS") from None
