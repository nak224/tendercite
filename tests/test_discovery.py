"""Deterministic metadata discovery and provenance-aware imports, never live provider calls."""

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

import httpx
import pytest
from discovery_helpers import NOTICE_ID, notice_payload, provider
from opportunity_helpers import create_input as manual_input
from opportunity_helpers import update_input
from pydantic import ValidationError

from tendercite.domain.discovery import TedSearchInput
from tendercite.domain.opportunities import OpportunityCreate, OpportunityError, OpportunityUpdate
from tendercite.domain.ted_reference import ted_opportunity_id, validate_notice_url
from tendercite.repositories.sqlite import SqliteRepository
from tendercite.services.discovery import TedNotImportable, import_ted, search_ted
from tendercite.services.ted import SEARCH_URL, TedError, TedResponseError, TedTimeoutError


@pytest.fixture
def repo(tmp_path):
    return SqliteRepository(tmp_path / "synthetic.db")


def test_official_bounded_keyword_page_payload_and_metadata():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={"notices": [notice_payload()], "totalNoticeCount": 6})

    result = search_ted(TedSearchInput(keyword="Software", page=2, limit=1), provider(handler))
    payload = json.loads(calls[0].content)
    assert str(calls[0].url) == SEARCH_URL and calls[0].method == "POST"
    assert payload["scope"] == "ACTIVE" and payload["paginationMode"] == "PAGE_NUMBER"
    assert payload["page"] == 2 and payload["limit"] == 1
    assert (
        payload["query"]
        == '(FT ~ "Software") AND (form-type = competition) SORT BY publication-date DESC'
    )
    assert "buyer-name" in payload["fields"] and payload["checkQuerySyntax"] is False
    assert result.next_page == 3 and result.total_notice_count == 6
    item = result.items[0]
    assert (
        item.title == "Synthetic software services"
        and item.organization == "Fictional public buyer"
    )
    assert item.summary == "Original synthetic software procurement summary."
    assert item.submission_deadline.isoformat() == "2099-03-01"
    assert item.source_url == f"https://ted.europa.eu/de/notice/-/detail/{NOTICE_ID}"
    assert item.importable is True and item.retrieved_at.tzinfo is not None


@pytest.mark.parametrize(
    "body",
    [
        {"keyword": ""},
        {"keyword": 'x" OR 1=1'},
        {"keyword": "x*"},
        {"keyword": "x\nOR y"},
        {"keyword": "x" * 151},
        {"keyword": "safe", "page": 0},
        {"keyword": "safe", "limit": 21},
        {"keyword": "safe", "page": 51},
        {"keyword": "safe", "url": "PRIVATE"},
    ],
)
def test_invalid_search_contracts(body):
    with pytest.raises(ValidationError):
        TedSearchInput.model_validate_json(json.dumps(body))


def test_empty_results_pagination_cap_and_no_unbounded_requests():
    empty = search_ted(
        TedSearchInput(keyword="No results"),
        provider(lambda _: httpx.Response(200, json={"notices": [], "totalNoticeCount": 0})),
    )
    assert empty.items == () and empty.next_page is None and empty.total_notice_count == 0
    result = search_ted(
        TedSearchInput(keyword="Software", page=50, limit=1),
        provider(
            lambda _: httpx.Response(
                200, json={"notices": [notice_payload()], "totalNoticeCount": 20000}
            )
        ),
    )
    assert result.next_page is None and result.pagination_capped is True


@pytest.mark.parametrize(
    "changes",
    [
        {"notice-title": []},
        {"buyer-name": []},
        {"description-proc": []},
        {"deadline-receipt-tender-date-lot": ["2099-02-30"]},
        {"deadline-receipt-tender-date-lot": ["2099-03-01T12:00:00Z"]},
    ],
)
def test_malformed_metadata_is_not_silently_repaired(changes):
    with pytest.raises(TedResponseError):
        search_ted(
            TedSearchInput(keyword="Software"),
            provider(
                lambda _: httpx.Response(
                    200, json={"notices": [notice_payload(**changes)], "totalNoticeCount": 1}
                )
            ),
        )


def test_missing_metadata_is_explicit_and_ambiguous_lot_deadlines_are_not_selected(repo):
    missing = provider(
        lambda _: httpx.Response(
            200, json={"notices": [{"publication-number": NOTICE_ID}], "totalNoticeCount": 1}
        )
    )
    item = search_ted(TedSearchInput(keyword="Software"), missing).items[0]
    assert (
        item.title
        is item.organization
        is item.summary
        is item.publication_date
        is item.submission_deadline
        is None
    )
    assert item.importable is False and "MISSING_TITLE" in item.warnings
    with pytest.raises(TedNotImportable):
        import_ted(NOTICE_ID, repo, missing)
    assert repo.list_opportunities().opportunities == ()
    multiple = provider(
        lambda _: httpx.Response(
            200,
            json={
                "notices": [
                    notice_payload(
                        **{"deadline-receipt-tender-date-lot": ["2099-03-01", "2099-04-01"]}
                    )
                ],
                "totalNoticeCount": 1,
            },
        )
    )
    item = search_ted(TedSearchInput(keyword="Software"), multiple).items[0]
    assert item.submission_deadline is None and "MULTIPLE_TENDER_DEADLINES" in item.warnings


def test_long_fields_are_shortened_explicitly_and_no_skills_are_extracted(repo):
    client = provider(
        lambda _: httpx.Response(
            200,
            json={
                "notices": [
                    notice_payload(
                        **{
                            "notice-title": {"deu": "T" * 201},
                            "buyer-name": {"deu": ["B" * 201]},
                            "description-proc": {"deu": "Python " * 400},
                        }
                    )
                ],
                "totalNoticeCount": 1,
            },
        )
    )
    result = import_ted(NOTICE_ID, repo, client)
    value = result.record.opportunity
    assert (
        len(value.title) == 200
        and len(value.organization) == 200
        and len(value.description) == 2000
    )
    assert value.required_skills == () and value.requirements_description == ""
    assert set(value.external_source.mapping_warnings) == {
        "TITLE_SHORTENED",
        "ORGANIZATION_SHORTENED",
        "SUMMARY_SHORTENED",
    }


@pytest.mark.parametrize("status", [302, 403, 429, 500])
def test_provider_errors_and_redirects_never_become_empty_results(status):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(status, headers={"Location": "https://example.invalid/PRIVATE"})

    with pytest.raises(TedError):
        search_ted(TedSearchInput(keyword="Software"), provider(handler))
    assert len(calls) == 1 and str(calls[0].url) == SEARCH_URL


def test_timeout_and_bounded_stream(monkeypatch):
    def timeout(request):
        raise httpx.ReadTimeout("PRIVATE", request=request)

    with pytest.raises(TedTimeoutError):
        search_ted(TedSearchInput(keyword="Software"), provider(timeout))
    from tendercite.services import ted

    monkeypatch.setattr(ted, "MAX_SEARCH_BYTES", 20)

    class Stream(httpx.SyncByteStream):
        def __iter__(self):
            yield b" " * 20
            yield b" "
            pytest.fail("Read beyond response byte budget")

    with pytest.raises(TedResponseError, match="4 MiB"):
        search_ted(
            TedSearchInput(keyword="Software"),
            provider(lambda _: httpx.Response(200, stream=Stream())),
        )


@pytest.mark.parametrize(
    "body",
    [
        b'{"notices":[],"notices":[],"totalNoticeCount":0}',
        b'{"notices":[],"totalNoticeCount":0,"timedOut":true}',
        b'{"notices":[],"totalNoticeCount":0,"timedOut":"false"}',
        b'{"PRIVATE":NaN}',
        b"PRIVATE",
    ],
)
def test_invalid_or_partial_json_never_reports_success(body):
    with pytest.raises(TedResponseError):
        search_ted(
            TedSearchInput(keyword="Software"),
            provider(lambda _: httpx.Response(200, content=body)),
        )


def test_import_identity_attribution_duplicate_after_edit_and_restart(repo):
    first = import_ted(NOTICE_ID, repo, provider())
    assert first.status == "CREATED" and first.record.opportunity_id == ted_opportunity_id(
        NOTICE_ID
    )
    original = first.record.opportunity
    assert (
        original.source_origin == "ted_import" and original.external_source.external_id == NOTICE_ID
    )
    changed = original.model_copy(
        update={"title": "My local revision", "required_skills": ("Python",)}
    )
    edited = repo.update_opportunity(first.record.opportunity_id, update_input(changed))
    before = repo.database_path.read_bytes()

    def forbidden(_):
        pytest.fail("Duplicate import performed a provider lookup")

    repeated = import_ted(
        NOTICE_ID, SqliteRepository(repo.database_path, initialize=False), provider(forbidden)
    )
    assert repeated.status == "ALREADY_PRESENT" and repeated.record == edited.record
    assert repo.database_path.read_bytes() == before
    assert (
        repo.list_pipeline().entries == ()
        and repo.list_findings() == []
        and repo.list_profiles().profiles == ()
    )


def test_source_changes_and_simultaneous_imports_never_overwrite(repo):
    def import_one(_):
        return import_ted(
            NOTICE_ID, SqliteRepository(repo.database_path, initialize=False), provider()
        ).status

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(import_one, range(2))) == ["ALREADY_PRESENT", "CREATED"]
    current = repo.get_opportunity(ted_opportunity_id(NOTICE_ID))
    changed = provider(
        lambda _: httpx.Response(
            200,
            json={
                "notices": [notice_payload(**{"notice-title": {"deu": "Changed provider title"}})],
                "totalNoticeCount": 1,
            },
        )
    )
    assert import_ted(NOTICE_ID, repo, changed).record == current
    assert len(repo.list_opportunities().opportunities) == 1


def test_provenance_cannot_be_forged_or_rewritten_and_manual_records_remain_supported(repo):
    imported = import_ted(NOTICE_ID, repo, provider()).record
    with pytest.raises(ValidationError):
        OpportunityCreate(opportunity_id=imported.opportunity_id, opportunity=imported.opportunity)
    source = imported.opportunity.external_source
    for replacement in (
        source.model_copy(update={"retrieved_at": datetime(2099, 1, 1, tzinfo=UTC)}),
        source.model_copy(
            update={
                "external_id": "99999992-2099",
                "original_url": "https://ted.europa.eu/de/notice/-/detail/99999992-2099",
            }
        ),
    ):
        request = OpportunityUpdate(
            expected_revision=1,
            opportunity=imported.opportunity.model_copy(update={"external_source": replacement}),
        )
        with pytest.raises(OpportunityError, match="PROVENANCE_CONFLICT"):
            repo.update_opportunity(imported.opportunity_id, request)
    manual = repo.create_opportunity(manual_input()).record
    assert (
        manual.opportunity.source_origin == "manual_operator_entry"
        and manual.opportunity.external_source is None
    )
    assert repo.get_opportunity(manual.opportunity_id) == manual


@pytest.mark.parametrize(
    "url",
    [
        "http://ted.europa.eu/de/notice/-/detail/99999991-2099",
        "https://ted.europa.eu.evil.invalid/de/notice/-/detail/99999991-2099",
        "https://user:secret@ted.europa.eu/de/notice/-/detail/99999991-2099",
        "https://ted.europa.eu/de/notice/-/detail/99999992-2099",
        "https://ted.europa.eu/de/notice/-/detail/99999991-2099?url=PRIVATE",
    ],
)
def test_official_source_links_are_bound_to_publication(url):
    with pytest.raises(ValueError):
        validate_notice_url(url, NOTICE_ID)
