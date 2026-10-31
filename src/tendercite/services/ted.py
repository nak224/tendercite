"""Small public TED Search API v3 adapter; no discovery scheduler or model calls."""

import json
import logging
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit

import httpx

from tendercite.domain.ted_reference import literal_keyword as literal_keyword
from tendercite.domain.ted_reference import publication_id as publication_id
from tendercite.domain.ted_reference import validate_notice_url as validate_notice_url

SEARCH_URL = "https://api.ted.europa.eu/v3/notices/search"
MAX_NOTICES = 50
MAX_DOCUMENT_BYTES = 25 * 1024 * 1024
MAX_SEARCH_BYTES = 4 * 1024 * 1024
REQUEST_INTERVAL_SECONDS = 1.0
FIELDS = (
    "publication-number",
    "notice-title",
    "official-language",
    "publication-date",
    "notice-type",
    "form-type",
    "classification-cpv",
    "links",
)
LANGUAGES = {
    "bul": "bg",
    "ces": "cs",
    "dan": "da",
    "deu": "de",
    "ell": "el",
    "eng": "en",
    "est": "et",
    "fin": "fi",
    "fra": "fr",
    "gle": "ga",
    "hrv": "hr",
    "hun": "hu",
    "ita": "it",
    "lav": "lv",
    "lit": "lt",
    "mlt": "mt",
    "nld": "nl",
    "pol": "pl",
    "por": "pt",
    "ron": "ro",
    "slk": "sk",
    "slv": "sl",
    "spa": "es",
    "swe": "sv",
}
logger = logging.getLogger(__name__)


class TedError(RuntimeError):
    """TED failures are errors, never successful empty search results."""

    def __init__(self, message, *, status_code=None):
        self.status_code = status_code
        super().__init__(message)


class TedResponseError(TedError):
    pass


class TedTimeoutError(TedError):
    pass


class TedNotFoundError(TedError):
    pass


def language_code(value: str) -> str:
    value = value.lower()
    return next((key for key, short in LANGUAGES.items() if value == short), value)


def validate_download_url(url: str, notice_id: str, format: str) -> str:
    """Only documented TED notice downloads; no attachments, userinfo or query URLs."""
    parsed = urlsplit(url)
    match = re.fullmatch(r"/([a-z]{2})/notice/([0-9]{1,8}-[0-9]{4})/(pdf|pdfs|xml)", parsed.path)
    if (
        parsed.scheme != "https"
        or parsed.netloc != "ted.europa.eu"
        or parsed.query
        or parsed.fragment
        or not match
        or match[1] not in LANGUAGES.values()
        or publication_id(match[2]) != publication_id(notice_id)
        or match[3] != format
    ):
        raise ValueError("Not an official TED document URL for this publication and format")
    return url


@dataclass(frozen=True)
class SearchFilters:
    country: str = "DEU"  # Place of performance, not buyer nationality.
    date_from: date | None = None
    date_to: date | None = None
    cpv: str = "72"  # Division/prefix, or an exact eight-digit CPV code.
    keyword: str | None = None
    limit: int = 20
    competition_only: bool = True

    def query(self) -> str:
        if not re.fullmatch(r"[A-Z]{3}", self.country):
            raise ValueError("country must be a three-letter TED country code, e.g. DEU")
        if not re.fullmatch(r"[0-9]{2,8}", self.cpv):
            raise ValueError("cpv must contain two to eight digits")
        if not 1 <= self.limit <= MAX_NOTICES:
            raise ValueError(f"limit must be between 1 and {MAX_NOTICES}")
        if self.date_from and self.date_to and self.date_from > self.date_to:
            raise ValueError("date_from must not be after date_to")
        cpv = self.cpv if len(self.cpv) == 8 else f"{self.cpv}*"
        parts = [f"(place-of-performance IN ({self.country}))", f"(classification-cpv = {cpv})"]
        if self.date_from:
            parts.append(f"(publication-date >= {self.date_from:%Y%m%d})")
        if self.date_to:
            parts.append(f"(publication-date <= {self.date_to:%Y%m%d})")
        if self.competition_only:
            parts.append("(form-type = competition)")
        if self.keyword is not None:
            # Restrict to a literal phrase; callers cannot inject expert-query operators.
            if len(self.keyword) > 150 or any(c in self.keyword for c in '\\"\r\n()'):
                raise ValueError("keyword must be a short literal phrase without query delimiters")
            if not self.keyword.strip():
                raise ValueError("keyword must not be blank")
            parts.append(f'(FT ~ "{self.keyword}")')
        return " AND ".join(parts) + " SORT BY publication-date DESC"


@dataclass(frozen=True)
class TedNotice:
    publication_id: str
    titles: dict[str, str]
    official_languages: tuple[str, ...]
    publication_date: str | None
    notice_type: str | None
    form_type: str | None
    cpv_codes: tuple[str, ...]
    source_url: str
    links: dict[str, dict[str, str]]
    warnings: tuple[str, ...] = ()
    buyers: dict[str, tuple[str, ...]] = field(default_factory=dict)
    descriptions: dict[str, str] = field(default_factory=dict)
    submission_dates: tuple[str, ...] = ()

    @property
    def title(self) -> str | None:
        return next(
            (self.titles[lang] for lang in self.official_languages if lang in self.titles),
            next(iter(self.titles.values()), None),
        )

    @property
    def available_formats(self) -> tuple[str, ...]:
        return tuple(sorted(key for key, values in self.links.items() if values))


@dataclass(frozen=True)
class SearchResult:
    query: str
    total_notice_count: int
    notices: tuple[TedNotice, ...]
    warnings: tuple[str, ...] = field(default_factory=tuple)


def _strings(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,) if value else ()
    if isinstance(value, dict) and isinstance(value.get("value"), str):
        return (value["value"],)
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return tuple(value)
    raise TedResponseError("Unexpected TED string/coded-value field shape")


def _notice(raw: object) -> TedNotice:
    if not isinstance(raw, dict):
        raise TedResponseError("Notice must be an object")
    try:
        ids = _strings(raw.get("publication-number"))
        if len(ids) != 1:
            raise ValueError("Missing publication number")
        notice_id = publication_id(ids[0])
    except ValueError as exc:
        raise TedResponseError("Invalid or missing publication number") from exc
    warnings = []
    titles = raw.get("notice-title")
    titles = {} if titles is None else titles
    if not isinstance(titles, dict) or not all(isinstance(v, str) for v in titles.values()):
        raise TedResponseError(f"{notice_id}: malformed notice-title")
    titles = {language_code(k): v for k, v in titles.items()}
    languages = tuple(language_code(v) for v in _strings(raw.get("official-language")))
    dates = _strings(raw.get("publication-date"))
    if dates:
        try:
            date.fromisoformat(dates[0][:10])  # TED may append a timezone to a date.
        except ValueError as exc:
            raise TedResponseError(f"{notice_id}: invalid publication-date") from exc
    links = raw.get("links")
    links = {} if links is None else links
    if not isinstance(links, dict):
        raise TedResponseError(f"{notice_id}: malformed links")
    safe_links = {}
    for format, values in links.items():
        if not isinstance(values, dict) or not all(isinstance(v, str) for v in values.values()):
            raise TedResponseError(f"{notice_id}: malformed links.{format}")
        safe_links[format] = {}
        for lang, url in values.items():
            if format in {"pdf", "pdfs", "xml"}:
                try:
                    validate_download_url(url, notice_id, format)
                except ValueError:
                    warnings.append(f"Rejected non-document URL in links.{format}.{lang}")
                    continue
                if (
                    format in {"pdf", "pdfs"}
                    and LANGUAGES.get(language_code(lang)) != (urlsplit(url).path.split("/")[1])
                ):
                    warnings.append(f"Rejected language-mismatched URL in links.{format}.{lang}")
                    continue
            safe_links[format][language_code(lang)] = url
    for name, value in [
        ("notice-title", titles),
        ("official-language", languages),
        ("publication-date", dates),
        ("links", safe_links),
    ]:
        if not value:
            warnings.append(f"Missing {name}")
    for warning in warnings:
        logger.warning("%s: %s", notice_id, warning)
    types = _strings(raw.get("notice-type"))
    forms = _strings(raw.get("form-type"))
    buyers = raw.get("buyer-name")
    descriptions = raw.get("description-proc")
    buyers = {} if buyers is None else buyers
    descriptions = {} if descriptions is None else descriptions
    if not isinstance(buyers, dict) or not all(isinstance(k, str) for k in buyers):
        raise TedResponseError("Malformed buyer metadata")
    if not isinstance(descriptions, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in descriptions.items()
    ):
        raise TedResponseError("Malformed description metadata")
    lang = next((LANGUAGES[v] for v in languages if v in LANGUAGES), "en")
    return TedNotice(
        notice_id,
        titles,
        languages,
        dates[0] if dates else None,
        types[0] if types else None,
        forms[0] if forms else None,
        _strings(raw.get("classification-cpv")),
        f"https://ted.europa.eu/{lang}/notice/-/detail/{notice_id}",
        safe_links,
        tuple(warnings),
        {language_code(k): _strings(v) for k, v in buyers.items()},
        {language_code(k): v for k, v in descriptions.items()},
        _strings(raw.get("deadline-receipt-tender-date-lot")),
    )


class TedClient:
    """Bounded retries, normal TLS verification and environment proxy support via HTTPX."""

    def __init__(
        self,
        client: httpx.Client | None = None,
        *,
        attempts: int = 3,
        sleep: Callable[[float], None] = time.sleep,
    ):
        if not 1 <= attempts <= 3:
            raise ValueError("attempts must be between 1 and 3")
        self.client = client or httpx.Client(
            timeout=httpx.Timeout(30, connect=10),
            headers={"User-Agent": "TenderCite-TED-evaluation/0.1"},
        )
        self._owns_client = client is None
        self.attempts = attempts
        self.sleep = sleep
        self._last_request = 0.0

    def __enter__(self):
        return self

    def __exit__(self, *args):
        if self._owns_client:
            self.client.close()

    def _pace(self):
        # At most one sequential request start per second, including retries and redirects.
        self.sleep(max(0, REQUEST_INTERVAL_SECONDS - (time.monotonic() - self._last_request)))
        self._last_request = time.monotonic()

    def download(self, url: str, notice_id: str, format: str) -> bytes:
        """Bounded streaming; revalidate each redirect before issuing another request."""
        validate_download_url(url, notice_id, format)
        original_language = urlsplit(url).path.split("/")[1]
        for attempt in range(self.attempts):
            current = url
            try:
                for redirect in range(4):
                    validate_download_url(current, notice_id, format)
                    if urlsplit(current).path.split("/")[1] != original_language:
                        raise TedError("TED download redirected to a different language")
                    self._pace()
                    with self.client.stream("GET", current, follow_redirects=False) as response:
                        if response.is_redirect:
                            if redirect == 3 or not response.headers.get("Location"):
                                raise TedError("TED download redirect limit or missing Location")
                            current = str(response.url.join(response.headers["Location"]))
                            continue
                        if action := response.headers.get("x-amzn-waf-action"):
                            raise TedError(
                                f"{current}: HTTP {response.status_code}; AWS WAF {action}; "
                                "document access blocked (no challenge bypass)"
                            )
                        if response.status_code in {429, 500, 502, 503, 504} and self.retry(
                            attempt, response
                        ):
                            break
                        if response.status_code != 200:
                            raise TedError(
                                f"{current}: HTTP {response.status_code}; download failed"
                            )
                        chunks = []
                        size = 0
                        for chunk in response.iter_bytes():
                            size += len(chunk)
                            if size > MAX_DOCUMENT_BYTES:
                                raise TedError(f"{current}: document exceeds 25 MiB limit")
                            chunks.append(chunk)
                        return b"".join(chunks)
            except ValueError as exc:
                raise TedError("Rejected unsafe TED download redirect") from exc
            except httpx.RequestError as exc:
                if isinstance(exc, httpx.ProxyError) or not self.retry(attempt):
                    raise TedError(f"{current}: {type(exc).__name__}: {exc}") from exc
        raise TedError("TED download retry budget exhausted")

    def retry(self, attempt: int, response: httpx.Response | None = None) -> bool:
        if attempt + 1 >= self.attempts:
            return False
        delay = float(2**attempt)
        if response is not None and (header := response.headers.get("Retry-After")):
            try:
                delay = max(delay, float(header))
            except ValueError:
                try:
                    delay = max(
                        delay, (parsedate_to_datetime(header) - datetime.now(UTC)).total_seconds()
                    )
                except (ValueError, TypeError):
                    pass
        # Do not retry early when a server asks us to wait beyond the bounded retry budget.
        if delay > 30:
            return False
        self.sleep(delay)
        return True

    def search(self, filters: SearchFilters) -> SearchResult:
        return self._search(filters.query(), filters.limit)

    def lookup(self, notice_id: str) -> TedNotice:
        notice_id = publication_id(notice_id)
        result = self._search(f'publication-number = "{notice_id}"', 1)
        if len(result.notices) != 1 or result.notices[0].publication_id != notice_id:
            raise TedResponseError(f"Publication {notice_id} was not returned by TED")
        return result.notices[0]

    def discover(self, keyword: str, *, limit=10, page=1) -> SearchResult:
        keyword = literal_keyword(keyword)
        if (
            type(limit) is not int
            or not 1 <= limit <= 20
            or type(page) is not int
            or not 1 <= page <= 50
        ):
            raise ValueError("Discovery page outside the bounded range")
        return self._search(
            f'(FT ~ "{keyword}") AND (form-type = competition) SORT BY publication-date DESC',
            limit,
            page=page,
            fields=DISCOVERY_FIELDS,
            scope="ACTIVE",
        )

    def lookup_discovery(self, notice_id: str) -> TedNotice:
        notice_id = publication_id(notice_id)
        result = self._search(f'publication-number = "{notice_id}"', 1, fields=DISCOVERY_FIELDS)
        if not result.notices and result.total_notice_count == 0:
            raise TedNotFoundError("TED publication not found")
        if len(result.notices) != 1 or result.notices[0].publication_id != notice_id:
            raise TedResponseError("TED publication was not returned")
        return result.notices[0]

    def _search(
        self, query: str, limit: int, *, page=1, fields=FIELDS, scope="ALL"
    ) -> SearchResult:
        payload = {
            "query": query,
            "fields": list(fields),
            "limit": limit,
            "page": page,
            "scope": scope,
            "paginationMode": "PAGE_NUMBER",
            "checkQuerySyntax": False,
        }
        for attempt in range(self.attempts):
            try:
                self._pace()
                with self.client.stream(
                    "POST", SEARCH_URL, json=payload, follow_redirects=False
                ) as response:
                    if response.status_code in {429, 500, 502, 503, 504} and self.retry(
                        attempt, response
                    ):
                        continue
                    if not response.is_success:
                        raise TedError(
                            f"{SEARCH_URL}: HTTP {response.status_code}; search did not succeed",
                            status_code=response.status_code,
                        )
                    chunks, size = [], 0
                    for chunk in response.iter_bytes():
                        size += len(chunk)
                        if size > MAX_SEARCH_BYTES:
                            raise TedResponseError("TED search response exceeds 4 MiB")
                        chunks.append(chunk)
                    raw_body = b"".join(chunks)
            except httpx.RequestError as exc:
                if isinstance(exc, httpx.ProxyError) or not self.retry(attempt):
                    if isinstance(exc, httpx.TimeoutException):
                        raise TedTimeoutError("TED search timed out") from None
                    raise TedError(f"{SEARCH_URL}: {type(exc).__name__}: {exc}") from None
                continue
            try:
                body = json.loads(
                    raw_body, object_pairs_hook=_unique_pairs, parse_constant=_bad_constant
                )
            except (ValueError, UnicodeError, RecursionError) as exc:
                raise TedResponseError("TED returned invalid JSON") from exc
            if (
                not isinstance(body, dict)
                or not isinstance(body.get("notices"), list)
                or ("error" in body and body["error"] is not None)
                or body.get("timedOut", False) is not False
            ):
                raise TedResponseError("TED response has no notices array")
            total = body.get("totalNoticeCount")
            if type(total) is not int or total < len(body["notices"]):
                raise TedResponseError("TED response has an invalid totalNoticeCount")
            if len(body["notices"]) > limit:
                raise TedResponseError("TED returned more notices than requested")
            notices = tuple(_notice(raw) for raw in body["notices"])
            if len({n.publication_id for n in notices}) != len(notices):
                raise TedResponseError("TED returned duplicate publication numbers")
            return SearchResult(query, total, notices)
        raise TedError("TED retry budget exhausted")


DISCOVERY_FIELDS = FIELDS + ("buyer-name", "description-proc", "deadline-receipt-tender-date-lot")


def _unique_pairs(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("Duplicate JSON key")
        value[key] = item
    return value


def _bad_constant(_):
    raise ValueError("Non-finite JSON number")
