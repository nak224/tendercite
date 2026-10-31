"""Self-authored OCDS-shaped fixtures; never copied procurement content."""

import io
import json
import zipfile

import httpx

from tendercite.domain.bkms_reference import LICENSE, POLICY, notice_parts
from tendercite.services.bkms import EXPORT_URL, BkmsClient

DAY = "2026-10-30"
NOTICE = "10000001-1"


def package(identity=NOTICE):
    notice_id, version = notice_parts(identity)
    return {
        "uri": f"https://oeffentlichevergabe.de/api/notices/{notice_id}?format=ocds&noticeVersion={version}",
        "version": "1.1",
        "license": LICENSE,
        "publicationPolicy": POLICY,
        "publishedDate": DAY + "T08:00:00Z",
        "releases": [
            {
                "id": notice_id,
                "ocid": "ocds-mnwr74-synthetic-" + notice_id,
                "tag": ["tender"],
                "buyer": {
                    "name": "Fictional public buyer",
                    "contactPoint": {"email": "do-not-retain@example.invalid"},
                },
                "tender": {
                    "title": "Synthetic software procurement",
                    "description": "Original synthetic Python procurement description.",
                    "tenderPeriod": {"endDate": "2026-12-04T12:00:00+01:00"},
                },
            }
        ],
    }


def archive(entries=None):
    entries = [(NOTICE + ".json", package())] if entries is None else entries
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as zip_file:
        for name, body in entries:
            value = body if isinstance(body, bytes) else json.dumps(body).encode("utf-8")
            zip_file.writestr(name, value)
    return output.getvalue()


def provider(payload=None, handler=None):
    def default(request):
        assert request.method == "GET" and str(request.url).split("?")[0] == EXPORT_URL
        assert dict(request.url.params) == {"pubDay": DAY, "format": "ocds.zip"}
        return httpx.Response(200, content=archive() if payload is None else payload)

    return BkmsClient(httpx.Client(transport=httpx.MockTransport(handler or default)))
