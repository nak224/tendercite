import re

_WHITESPACE_RE = re.compile(r"[ \t\f\v]+")
_BLANK_LINES_RE = re.compile(r"\n{3,}")


def normalize_page_text(text: str) -> str:
    """Normalize PDF-extracted text while preserving page boundaries."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = [_WHITESPACE_RE.sub(" ", line).strip() for line in text.split("\n")]
    normalized = "\n".join(lines)
    normalized = _BLANK_LINES_RE.sub("\n\n", normalized)
    return normalized.strip()


def normalize_quote(text: str) -> str:
    return " ".join(text.split())
