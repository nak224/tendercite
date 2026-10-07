from tendercite.core.config import settings
from tendercite.repositories.sqlite import SqliteRepository

_repository: SqliteRepository | None = None


def get_repository() -> SqliteRepository:
    global _repository
    if _repository is None:
        _repository = SqliteRepository(settings.database_path)
    return _repository
