"""Official TED metadata mapping and explicit import, without inferred skills or syncing."""

import re
from datetime import UTC, date, datetime

from tendercite.domain.discovery import TedDiscoveryItem, TedSearchInput, TedSearchResult
from tendercite.domain.opportunities import (
    NativeOpportunity,
    OpportunitySaveResult,
    OpportunityType,
    TedOpportunitySource,
)
from tendercite.services.ted import TedClient, TedNotice, TedResponseError


class TedNotImportable(ValueError):
    pass


def _date(value: str) -> date:
    if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}(?:Z|[+-][0-9]{2}:[0-9]{2})?", value):
        raise TedResponseError("Invalid TED date-only metadata")
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        raise TedResponseError("Invalid TED date-only metadata") from None


def _language_value(values: dict, languages):
    for language in (*languages, "deu", "eng", *sorted(values)):
        if values.get(language):
            return values[language]
    return None


def discovery_item(notice: TedNotice) -> TedDiscoveryItem:
    warnings = []

    def bounded(value, limit, missing, shortened):
        value = value.strip() if value else None
        if not value:
            warnings.append(missing)
            return None
        if len(value) > limit:
            warnings.append(shortened)
        return value[:limit]

    title = bounded(notice.title, 200, "MISSING_TITLE", "TITLE_SHORTENED")
    buyers = _language_value(notice.buyers, notice.official_languages)
    organization = bounded(
        "; ".join(buyers) if buyers else None, 200, "MISSING_BUYER", "ORGANIZATION_SHORTENED"
    )
    summary = bounded(
        _language_value(notice.descriptions, notice.official_languages),
        2000,
        "MISSING_DESCRIPTION",
        "SUMMARY_SHORTENED",
    )
    published = _date(notice.publication_date) if notice.publication_date else None
    if published is None:
        warnings.append("MISSING_PUBLICATION_DATE")
    dates = sorted({_date(value) for value in notice.submission_dates})
    deadline = dates[0] if len(dates) == 1 else None
    if not dates:
        warnings.append("MISSING_DEADLINE")
    elif len(dates) > 1:
        warnings.append("MULTIPLE_TENDER_DEADLINES")
    return TedDiscoveryItem(
        publication_id=notice.publication_id,
        title=title,
        organization=organization,
        summary=summary,
        publication_date=published,
        submission_deadline=deadline,
        source_url=notice.source_url,
        retrieved_at=datetime.now(UTC),
        importable=title is not None,
        warnings=tuple(warnings),
    )


def search_ted(request: TedSearchInput, client: TedClient) -> TedSearchResult:
    result = client.discover(request.keyword, limit=request.limit, page=request.page)
    more = (
        len(result.notices) == request.limit
        and request.page * request.limit < result.total_notice_count
    )
    return TedSearchResult(
        keyword=request.keyword,
        page=request.page,
        limit=request.limit,
        total_notice_count=result.total_notice_count,
        items=tuple(discovery_item(n) for n in result.notices),
        next_page=request.page + 1 if more and request.page < 50 else None,
        pagination_capped=result.total_notice_count > request.limit * 50,
    )


def import_ted(notice_id: str, repository, client: TedClient) -> OpportunitySaveResult:
    if existing := repository.find_ted_opportunity(notice_id):
        return existing
    item = discovery_item(client.lookup_discovery(notice_id))
    if not item.importable:
        raise TedNotImportable("TED_NOTICE_NOT_IMPORTABLE")
    opportunity = NativeOpportunity(
        title=item.title,
        opportunity_type=OpportunityType.PUBLIC_TENDER,
        organization=item.organization or "",
        description=item.summary or "",
        deadline=item.submission_deadline,
        source_platform="TED",
        source_url=item.source_url,
        source_origin="ted_import",
        external_source=TedOpportunitySource(
            external_id=item.publication_id,
            original_url=item.source_url,
            publication_date=item.publication_date,
            retrieved_at=item.retrieved_at,
            mapping_warnings=item.warnings,
        ),
    )
    return repository.import_ted_opportunity(opportunity)
