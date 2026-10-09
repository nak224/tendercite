"""Opt-in public API smoke test. No model, LLM, key or document download."""

import os

import pytest

from tendercite.services.ted import SearchFilters, TedClient


@pytest.mark.live
@pytest.mark.skipif(
    os.environ.get("TENDERCITE_TED_LIVE") != "1" or os.environ.get("HF_HUB_OFFLINE") == "1",
    reason="Explicit opt-in only; excluded from offline CI",
)
def test_public_ted_search_smoke():
    with TedClient() as client:
        result = client.search(SearchFilters(limit=1))
    assert result.total_notice_count >= 1 and len(result.notices) == 1
    assert result.notices[0].publication_id
