"""Self-authored TED-shaped metadata. These future IDs are fictional test records."""

import httpx

from tendercite.services.ted import TedClient

NOTICE_ID = "99999991-2099"


def notice_payload(notice_id=NOTICE_ID, **changes):
    return {
        "publication-number": notice_id,
        "notice-title": {"deu": "Synthetic software services"},
        "official-language": ["DEU"],
        "publication-date": "2099-02-01+01:00",
        "buyer-name": {"deu": ["Fictional public buyer"]},
        "description-proc": {"deu": "Original synthetic software procurement summary."},
        "deadline-receipt-tender-date-lot": ["2099-03-01+01:00"],
        "links": {},
        **changes,
    }


def provider(handler=None):
    handler = handler or (
        lambda request: httpx.Response(
            200, json={"notices": [notice_payload()], "totalNoticeCount": 1}
        )
    )
    return TedClient(
        httpx.Client(transport=httpx.MockTransport(handler)), attempts=1, sleep=lambda _: None
    )
