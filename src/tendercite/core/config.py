from pathlib import Path

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="TENDERCITE_", env_file=".env", extra="ignore")

    app_name: str = "TenderCite"
    data_dir: Path = Path("./data")
    max_upload_mb: int = Field(default=30, ge=1, le=100)
    max_pdf_pages: int = Field(default=500, ge=1, le=5000)
    max_extracted_chars: int = Field(default=2_000_000, ge=1)
    llm_base_url: str | None = None
    llm_model: str | None = None
    llm_api_key: SecretStr | None = None
    retrieval_enabled: bool = True
    embedding_model: str = "intfloat/multilingual-e5-small"
    embedding_revision: str | None = None

    @field_validator("llm_base_url")
    @classmethod
    def validate_provider_url(cls, value):
        from urllib.parse import urlsplit

        if not value:
            return None
        url = urlsplit(value)
        if (
            url.scheme not in ("http", "https")
            or not url.hostname
            or url.username
            or url.password
            or url.query
            or url.fragment
        ):
            raise ValueError("Use an HTTP(S) base URL without credentials, query or fragment")
        return value

    @property
    def upload_dir(self) -> Path:
        return self.data_dir / "uploads"

    @property
    def database_path(self) -> Path:
        return self.data_dir / "tendercite.db"


settings = Settings()
