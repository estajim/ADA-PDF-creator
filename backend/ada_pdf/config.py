from enum import Enum
from pathlib import Path
from typing import Optional

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class OCRProvider(str, Enum):
    SURYA = "surya"
    PADDLEOCR = "paddleocr"


class ColumnAlgorithm(str, Enum):
    GAP = "gap"
    KMEANS = "kmeans"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Simple mode: SQLite + BackgroundTasks (no Postgres/Redis/Celery needed)
    SIMPLE_MODE: bool = False

    # Database — defaults to PostgreSQL; override with sqlite+aiosqlite:///./ada_pdf.db in simple mode
    DATABASE_URL: str = "postgresql+asyncpg://ada_pdf:secret@localhost:5432/ada_pdf"

    # Redis / Celery
    REDIS_URL: str = "redis://localhost:6379/0"
    CELERY_BROKER_URL: str = "redis://localhost:6379/1"
    CELERY_RESULT_BACKEND: str = "redis://localhost:6379/2"

    # Anthropic
    ANTHROPIC_API_KEY: str = Field(repr=False)

    # Storage
    STORAGE_ROOT: Path = Path("/app/storage")
    MAX_FILE_SIZE_MB: int = 100

    # veraPDF
    VERAPDF_CLI_PATH: str = "/opt/verapdf/verapdf"

    # PAC 2024 (PDF/UA Foundation) — set to the binary inside the .app bundle on Mac,
    # or leave empty to auto-detect at the default Mac/Windows install path.
    PAC_2024_CLI_PATH: str = ""

    # OCR
    OCR_PRIMARY: OCRProvider = OCRProvider.SURYA
    SURYA_MODEL_DIR: Optional[Path] = None
    TESSERACT_CMD: Optional[str] = None        # full path, e.g. /opt/homebrew/bin/tesseract

    # Rate limiting
    RATE_LIMIT_PER_MINUTE: int = 10

    # CORS — comma-separated allowed origins; "*" permits all (dev only)
    CORS_ALLOWED_ORIGINS: str = "*"

    # Security
    CLAMAV_SOCKET_PATH: Optional[str] = None

    # Storage cleanup TTL in hours (0 = never purge)
    STORAGE_TTL_HOURS: int = 24

    # Large PDF: max pages to process in one pass before chunking
    MAX_PAGES_PER_CHUNK: int = 50

    # Feature flags
    WEASYPRINT_ENABLED: bool = True
    REPORTLAB_FALLBACK: bool = True
    WEASYPRINT_TIMEOUT_SECONDS: int = 120

    # Pipeline
    COLUMN_ALGORITHM: ColumnAlgorithm = ColumnAlgorithm.GAP
    CELERY_TASK_MAX_RETRIES: int = 3

    @field_validator("STORAGE_ROOT", mode="after")
    @classmethod
    def ensure_storage_exists(cls, v: Path) -> Path:
        v.mkdir(parents=True, exist_ok=True)
        return v

    @property
    def max_file_size_bytes(self) -> int:
        return self.MAX_FILE_SIZE_MB * 1024 * 1024


def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
