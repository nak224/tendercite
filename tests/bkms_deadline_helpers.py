"""Original minimal XML fixtures exercising documented paths, not full eForms notices."""

from xml.etree import ElementTree as ET

from bkms_helpers import NOTICE, archive

from tendercite.services.bkms_deadlines import NS


def xml(identity=NOTICE, lots=None, customization="eforms-de-2.1"):
    identifier, version = identity.rsplit("-", 1)
    root = ET.Element(
        "{urn:oasis:names:specification:ubl:schema:xsd:ContractNotice-2}ContractNotice"
    )
    for key, text in (
        ("ID", identifier),
        ("VersionID", version),
        ("CustomizationID", customization),
    ):
        field = ET.SubElement(root, f"{{{NS['cbc']}}}{key}")
        field.text = text
        if key == "ID":
            field.set("schemeName", "notice-id")
    ET.SubElement(root, f"{{{NS['cbc']}}}Note").text = "PRIVATE synthetic contact"
    lots = [("LOT-0001", [("2026-12-04+01:00", "10:00:00+01:00")])] if lots is None else lots
    for identity, values in lots:
        lot = ET.SubElement(root, f"{{{NS['cac']}}}ProcurementProjectLot")
        ET.SubElement(lot, f"{{{NS['cbc']}}}ID", schemeName="Lot").text = identity
        process = ET.SubElement(lot, f"{{{NS['cac']}}}TenderingProcess")
        for day, clock in values:
            period = ET.SubElement(process, f"{{{NS['cac']}}}TenderSubmissionDeadlinePeriod")
            for key, value in (("EndDate", day), ("EndTime", clock)):
                if value is not None:
                    ET.SubElement(period, f"{{{NS['cbc']}}}{key}").text = value
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def eforms_archive(body=None, identity=NOTICE):
    return archive([(identity + ".xml", xml(identity) if body is None else body)])
