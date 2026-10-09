"""Synthetic API responses only; publication IDs here are not curated real notices."""

import json
from datetime import date

import httpx
import pytest

from tendercite.services import ted
from tendercite.services.ted import (
    SEARCH_URL,
    SearchFilters,
    TedClient,
    TedError,
    TedResponseError,
    publication_id,
    validate_download_url,
)


def notice_payload(notice_id="123456-2026", language="DEU"):
    return {
        "publication-number": notice_id,
        "notice-title": {"deu": "IT-Dienstleistungen für die Verwaltung", "eng": "IT services"},
        "official-language": [language],
        "publication-date": ["2026-07-01+02:00"],
        "notice-type": "cn-standard",
        "form-type": "competition",
        "classification-cpv": ["72200000"],
        "links": {
            "pdf": {"DEU": f"https://ted.europa.eu/de/notice/{notice_id}/pdf"},
            "xml": {"MUL": f"https://ted.europa.eu/en/notice/{notice_id}/xml"},
        },
    }


def client_for(handler, *, delays=None):
    return TedClient(
        httpx.Client(transport=httpx.MockTransport(handler)),
        sleep=(delays.append if delays is not None else lambda _: None),
    )


def response(*notices):
    return httpx.Response(200, json={"notices": list(notices), "totalNoticeCount": len(notices)})


def test_search_query_payload_and_typed_metadata():
    requests = []

    def handler(request):
        requests.append(request)
        return response(notice_payload("00123456-2026"))

    result = client_for(handler).search(
        SearchFilters(
            date_from=date(2026, 7, 1), date_to=date(2026, 9, 30), keyword="Software", limit=15
        )
    )
    assert str(requests[0].url) == SEARCH_URL
    assert requests[0].method == "POST"
    body = json.loads(requests[0].content)
    assert body["query"] == (
        "(place-of-performance IN (DEU)) AND (classification-cpv = 72*) "
        "AND (publication-date >= 20260701) AND (publication-date <= 20260930) "
        'AND (form-type = competition) AND (FT ~ "Software") SORT BY publication-date DESC'
    )
    assert body["limit"] == 15 and body["page"] == 1 and body["scope"] == "ALL"
    assert body["checkQuerySyntax"] is False
    assert body["fields"] == list(ted.FIELDS)
    notice = result.notices[0]
    assert notice.publication_id == "123456-2026"
    assert notice.title == "IT-Dienstleistungen für die Verwaltung"
    assert notice.official_languages == ("deu",)
    assert notice.publication_date == "2026-07-01+02:00"
    assert notice.available_formats == ("pdf", "xml")
    assert notice.source_url == "https://ted.europa.eu/de/notice/-/detail/123456-2026"


@pytest.mark.parametrize(
    "kwargs",
    [
        {"limit": 0},
        {"limit": 51},
        {"country": "DE"},
        {"cpv": "72*"},
        {"cpv": "7"},
        {"keyword": 'x" OR 1=1'},
        {"keyword": ""},
        {"date_from": date(2026, 9, 1), "date_to": date(2026, 7, 1)},
    ],
)
def test_invalid_filters_never_issue_request(kwargs):
    with pytest.raises(ValueError):
        client_for(lambda _: pytest.fail("Unexpected request")).search(SearchFilters(**kwargs))


def test_exact_cpv_and_other_forms():
    query = SearchFilters(cpv="72200000", competition_only=False).query()
    assert "classification-cpv = 72200000" in query and "form-type" not in query


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"notices": None, "totalNoticeCount": 0},
        {"notices": [], "totalNoticeCount": True},
        {"notices": [], "totalNoticeCount": -1},
        {"notices": [{}], "totalNoticeCount": 1},
        {"notices": ["bad"], "totalNoticeCount": 1},
        {"notices": [notice_payload(), notice_payload()], "totalNoticeCount": 2},
    ],
)
def test_malformed_responses_are_errors(body):
    with pytest.raises(TedResponseError):
        client_for(lambda _: httpx.Response(200, json=body)).search(SearchFilters(limit=1))


def test_invalid_json_and_valid_empty_response_are_distinct():
    with pytest.raises(TedResponseError, match="JSON"):
        client_for(lambda _: httpx.Response(200, text="not JSON")).search(SearchFilters())
    result = client_for(lambda _: response()).search(SearchFilters())
    assert result.notices == () and result.total_notice_count == 0


@pytest.mark.parametrize(
    "field,value",
    [
        ("notice-title", ["title"]),
        ("notice-title", []),
        ("publication-date", ["2026-02-30"]),
        ("official-language", 4),
        ("links", []),
        ("links", {"pdf": "url"}),
    ],
)
def test_malformed_notice_fields_are_errors(field, value):
    raw = notice_payload()
    raw[field] = value
    with pytest.raises(TedResponseError):
        client_for(lambda _: response(raw)).search(SearchFilters())


def test_missing_optional_metadata_is_logged_and_preserved(caplog):
    raw = {"publication-number": "123456-2026"}
    notice = client_for(lambda _: response(raw)).search(SearchFilters()).notices[0]
    assert notice.title is None and notice.publication_date is None
    assert notice.official_languages == () and "Missing official-language" in notice.warnings
    assert "Missing notice-title" in caplog.text


@pytest.mark.parametrize("status", [400, 403, 404, 429, 500, 503])
def test_http_errors_never_become_zero_results(status):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(status)

    with pytest.raises(TedError, match=f"HTTP {status}"):
        client_for(handler).search(SearchFilters())
    assert len(calls) == (3 if status in {429, 500, 503} else 1)


def test_retry_after_and_timeout_recovery():
    requests, delays = [], []

    def handler(request):
        requests.append(request)
        if len(requests) == 1:
            return httpx.Response(429, headers={"Retry-After": "7"})
        if len(requests) == 2:
            raise httpx.ReadTimeout("timeout", request=request)
        return response(notice_payload())

    assert client_for(handler, delays=delays).search(SearchFilters()).notices
    assert len(requests) == 3 and 7 in delays and 2 in delays


def test_long_retry_after_does_not_retry_early():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(429, headers={"Retry-After": "120"})

    with pytest.raises(TedError, match="HTTP 429"):
        client_for(handler).search(SearchFilters())
    assert len(calls) == 1


def test_proxy_denial_identifies_destination_without_retrying():
    calls = []

    def handler(request):
        calls.append(request)
        raise httpx.ProxyError("403 Forbidden", request=request)

    with pytest.raises(TedError, match=r"api\.ted\.europa\.eu.*ProxyError.*403"):
        client_for(handler).search(SearchFilters())
    assert len(calls) == 1


def test_fixed_id_lookup_and_missing_notice():
    requests = []

    def handler(request):
        requests.append(json.loads(request.content))
        return response(notice_payload())

    notice = client_for(handler).lookup("00123456-2026")
    assert notice.publication_id == "123456-2026"
    assert requests[0]["query"] == 'publication-number = "123456-2026"'
    with pytest.raises(TedError, match="not returned"):
        client_for(lambda _: response()).lookup("123456-2026")
    assert publication_id("00123456-2026") == "123456-2026"


@pytest.mark.parametrize(
    "url",
    [
        "http://ted.europa.eu/de/notice/123456-2026/pdf",
        "https://ted.europa.eu.evil.test/de/notice/123456-2026/pdf",
        "https://user@ted.europa.eu/de/notice/123456-2026/pdf",
        "https://ted.europa.eu:443/de/notice/123456-2026/pdf",
        "https://ted.europa.eu/de/notice/999999-2026/pdf",
        "https://ted.europa.eu/de/notice/123456-2026/pdf?url=https://evil.test",
        "https://ted.europa.eu/de/notice/123456-2026/pdf#fragment",
        "https://ted.europa.eu/de/notice/123456-2026/xml",
        "https://ted.europa.eu/attachments/file.pdf",
    ],
)
def test_reject_nonofficial_or_wrong_document_urls(url):
    with pytest.raises(ValueError):
        validate_download_url(url, "123456-2026", "pdf")


def test_unsafe_download_links_are_removed_and_logged(caplog):
    raw = notice_payload()
    raw["links"]["pdf"]["DEU"] = "https://attachments.test/a.pdf"
    notice = client_for(lambda _: response(raw)).search(SearchFilters()).notices[0]
    assert notice.links["pdf"] == {} and "Rejected non-document URL" in caplog.text


def test_download_link_language_must_match_api_language_key():
    raw = notice_payload()
    raw["links"]["pdf"]["DEU"] = "https://ted.europa.eu/en/notice/123456-2026/pdf"
    notice = client_for(lambda _: response(raw)).search(SearchFilters()).notices[0]
    assert notice.links["pdf"] == {}
    assert any("language-mismatched" in warning for warning in notice.warnings)


@pytest.mark.parametrize(
    "target",
    [
        "https://attachments.test/a.pdf",
        "/en/notice/123456-2026/pdf",
        "/de/notice/999999-2026/pdf",
        "/de/notice/123456-2026/xml",
    ],
)
def test_redirects_are_validated_before_following(target):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(302, headers={"Location": target})

    with pytest.raises(TedError):
        client_for(handler).download(
            "https://ted.europa.eu/de/notice/123456-2026/pdf", "123456-2026", "pdf"
        )
    assert len(calls) == 1


def test_download_limit_retry_and_stream_failure(monkeypatch):
    monkeypatch.setattr(ted, "MAX_DOCUMENT_BYTES", 10)
    url = "https://ted.europa.eu/de/notice/123456-2026/pdf"
    calls = []

    def handler(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(503)
        return httpx.Response(200, content=b"12345678901")

    with pytest.raises(TedError, match="exceeds"):
        client_for(handler).download(url, "123456-2026", "pdf")
    assert len(calls) == 2


def test_redirect_loop_is_bounded():
    url = "https://ted.europa.eu/de/notice/123456-2026/pdf"
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(302, headers={"Location": url})

    with pytest.raises(TedError, match="redirect limit"):
        client_for(handler).download(url, "123456-2026", "pdf")
    assert len(calls) == 4


@pytest.mark.parametrize(
    "headers,expected",
    [
        ({"x-amzn-waf-action": "challenge"}, "AWS WAF challenge"),
        ({}, "HTTP 202"),
    ],
)
def test_accepted_or_waf_challenge_response_is_not_a_download(headers, expected):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(202, headers=headers)

    with pytest.raises(TedError, match=expected):
        client_for(handler).download(
            "https://ted.europa.eu/de/notice/123456-2026/pdf", "123456-2026", "pdf"
        )
    assert len(calls) == 1
