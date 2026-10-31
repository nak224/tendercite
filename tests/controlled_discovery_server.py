"""Opt-in browser harness: controlled synthetic TED HTTP responses + actual application/SQLite.

Never exposed by production configuration. Refuses existing/non-temporary databases.
"""

import argparse
import json
import os
import re
import tempfile
from pathlib import Path

import httpx
import uvicorn
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

    def german_handler(request):
        assert request.method == "GET" and str(request.url).split("?")[0] == EXPORT_URL
        assert dict(request.url.params) == {"pubDay": "2026-10-30", "format": "ocds.zip"}
        return httpx.Response(200, content=synthetic_archive)

    def controlled_german_client():
        with httpx.Client(transport=httpx.MockTransport(german_handler)) as http:
            with BkmsClient(http) as client:
                yield client

    app.dependency_overrides[get_bkms_client] = controlled_german_client
    print("CONTROLLED SYNTHETIC OFFICIAL PROVIDERS: browser tests do not contact live services.")
    uvicorn.run(app, host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
