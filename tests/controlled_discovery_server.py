"""Opt-in browser harness: controlled synthetic TED HTTP responses + actual application/SQLite.

Never exposed by production configuration. Refuses existing/non-temporary databases.
"""

import argparse
import io
import json
import os
import re
import tempfile
import zipfile
from pathlib import Path

import httpx
import uvicorn
from bkms_deadline_helpers import xml
from bkms_helpers import archive, package
from discovery_helpers import notice_payload

from tendercite.api.routes.bkms import get_bkms_client
from tendercite.api.routes.discovery import get_ted_client
from tendercite.core.config import settings
from tendercite.main import app
from tendercite.services.bkms import EXPORT_URL, BkmsClient
from tendercite.services.ted import SEARCH_URL, TedClient


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=18015)
    args = parser.parse_args()
    directory = settings.data_dir.resolve()
    if (
        os.getenv("TENDERCITE_CONTROLLED_DISCOVERY_SMOKE") != "1"
        or not 1 <= args.port <= 65535
        or directory.parent != Path(tempfile.gettempdir()).resolve()
        or not directory.name.startswith("tendercite-discovery-")
        or settings.database_path.exists()
        or settings.retrieval_enabled
        or settings.llm_base_url
        or settings.llm_model
    ):
        parser.error(
            "Requires opt-in, fresh task-owned temporary data and disabled retrieval/providers"
        )
    notices = [
        notice_payload(
            f"9999999{i}-2099",
            **{
                "notice-title": {
                    "deu": (
                        "Synthetic software services"
                        if i == 1
                        else f"Fictional software notice {i}"
                    )
                }
            },
        )
        for i in range(1, 7)
    ]

    def handler(request):
        assert str(request.url) == SEARCH_URL and request.method == "POST"
        body = json.loads(request.content)
        if match := re.fullmatch(r'publication-number = "([0-9]+-[0-9]+)"', body["query"]):
            values = [n for n in notices if n["publication-number"] == match[1]]
            total = len(values)
        elif 'FT ~ "Software"' in body["query"]:
            start = (body["page"] - 1) * body["limit"]
            values = notices[start : start + body["limit"]]
            total = len(notices)
        else:
            values = []
            total = 0
        return httpx.Response(
            200, json={"notices": values, "totalNoticeCount": total, "timedOut": False}
        )

    def controlled_client():
        with httpx.Client(transport=httpx.MockTransport(handler)) as http:
            with TedClient(http, attempts=1, sleep=lambda _: None) as client:
                yield client

    app.dependency_overrides[get_ted_client] = controlled_client
    synthetic_archive = archive(
        [(f"1000000{i}-1.json", package(f"1000000{i}-1")) for i in range(1, 7)]
    )

    def later_record(identity, tags):
        value = package(identity)
        value["publishedDate"] = "2026-10-31T08:00:00Z"
        value["releases"][0]["tag"] = tags
        value["releases"][0]["tender"].pop("tenderPeriod")
        return value

    daily_archives = {
        "2026-10-30": synthetic_archive,
        "2026-10-31": archive(
            [
                ("10000001-2.json", later_record("10000001-2", ["award"])),
                ("10000002-1.json", package("10000002-1")),
                ("10000008-1.json", later_record("10000008-1", [])),
            ]
        ),
    }

    def german_handler(request):
        assert request.method == "GET" and str(request.url).split("?")[0] == EXPORT_URL
        assert set(request.url.params) == {"pubDay", "format"}
        assert request.url.params["format"] in ("ocds.zip", "eforms.zip")
        body = daily_archives.get(request.url.params["pubDay"])
        if body and request.url.params["format"] == "eforms.zip":
            records = []
            with zipfile.ZipFile(io.BytesIO(body)) as source:
                for name in source.namelist():
                    identity = name[:-5]
                    options = {}
                    if identity == "10000002-1":
                        options["lots"] = [
                            ("LOT-0001", [("2026-12-08+01:00", "10:00:00+01:00")]),
                            ("LOT-0002", [("2026-12-09+01:00", "11:00:00+01:00")]),
                        ]
                    elif identity == "10000003-1":
                        options["lots"] = [("LOT-0001", [("2026-12-08+01:00", "10:00:00+02:00")])]
                    elif identity == "10000004-1":
                        options["lots"] = []
                    elif identity == "10000005-1":
                        options["customization"] = "eforms-sdk-0.1"
                    records.append((identity + ".xml", xml(identity, **options)))
            body = archive(records)
        return httpx.Response(200, content=body) if body else httpx.Response(400)

    def controlled_german_client():
        with httpx.Client(transport=httpx.MockTransport(german_handler)) as http:
            with BkmsClient(http) as client:
                yield client

    app.dependency_overrides[get_bkms_client] = controlled_german_client
    print("CONTROLLED SYNTHETIC OFFICIAL PROVIDERS: browser tests do not contact live services.")
    uvicorn.run(app, host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
