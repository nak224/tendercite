from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class SearchHit:
    chunk_id: str
    document_id: str
    page_number: int
    text: str
    score: float


class Retriever(Protocol):
    def index(self, *, chunk_id: str, document_id: str, page_number: int, text: str) -> None: ...

    def search(self, query: str, *, limit: int = 8) -> list[SearchHit]: ...
