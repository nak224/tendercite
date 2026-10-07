from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="TENDERCITE_", env_file=".env", extra="ignore")

    app_name: str = "TenderCite"
    data_dir: Path = Path("./data")
    max_upload_mb: int = 30
    retrieval_enabled: bool = True
    embedding_model: str = "intfloat/e5-small-v2"
    embedding_revision: str | None = None

    @property
    def upload_dir(self) -> Path:
        return self.data_dir / "uploads"

    @property
    def database_path(self) -> Path:
        return self.data_dir / "tendercite.db"


settings = Settings()
