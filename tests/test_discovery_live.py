"""Opt-in, free official metadata reads only; excluded from offline CI."""

import os

import pytest

from tendercite.domain.discovery import TedSearchInput
from tendercite.services.discovery import search_ted
from tendercite.services.ted import TedClient


@pytest.mark.live
@pytest.mark.skipif(
    os.getenv("TENDERCITE_LIVE_TED_DISCOVERY") != "1", reason="Explicit live TED opt-in required"
)
def test_official_ted_discovery_two_pages():
    with TedClient(attempts=1) as client:
        for page in (1, 2):
            result = search_ted(TedSearchInput(keyword="Software", limit=2, page=page), client)
            assert result.page == page and len(result.items) <= 2
            assert all(
                item.source_url.startswith("https://ted.europa.eu/") for item in result.items
            )
