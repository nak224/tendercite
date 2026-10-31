"""Pure TED publication identity/reference rules; no HTTP client or provider configuration."""

import re
from urllib.parse import urlsplit
from uuid import NAMESPACE_URL, uuid5

TED_LANGUAGES = frozenset(
    "bg cs da de el en es et fi fr ga hr hu it lt lv mt nl pl pt ro sk sl sv".split()
)


def literal_keyword(keyword: str) -> str:
    if (
        not isinstance(keyword, str)
        or not 1 <= len(keyword) <= 150
        or not re.fullmatch(r"[\w .+\-]+", keyword)
        or not keyword.strip()
    ):
        raise ValueError("Use a bounded literal keyword phrase")
    return " ".join(keyword.split())


def publication_id(value: str) -> str:
    if (
        not isinstance(value, str)
        or not re.fullmatch(r"[0-9]{1,8}-[0-9]{4}", value)
        or int(value.split("-")[0]) == 0
    ):
        raise ValueError("Expected a TED publication number such as 123456-2026")
    number, year = value.split("-")
    return f"{int(number)}-{year}"


def validate_notice_url(url: str, notice_id: str) -> str:
    parsed = urlsplit(url)
    match = re.fullmatch(r"/([a-z]{2})/notice/-/detail/([0-9]{1,8}-[0-9]{4})", parsed.path)
    if (
        parsed.scheme != "https"
        or parsed.netloc != "ted.europa.eu"
        or parsed.query
        or parsed.fragment
        or not match
        or match[1] not in TED_LANGUAGES
        or publication_id(match[2]) != publication_id(notice_id)
    ):
        raise ValueError("Not an official TED notice reference")
    return url


def ted_opportunity_id(notice_id: str):
    return uuid5(NAMESPACE_URL, f"https://ted.europa.eu/notice/{publication_id(notice_id)}")
