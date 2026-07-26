"""Application configuration.

Every environment variable the application reads is declared here, typed, and
validated at import time. A missing or malformed required value aborts startup
with a named error instead of failing later at first use.

Adding a variable means touching three places (CLAUDE.md):
this file, ``.env.example``, and the table in ``docs/SPEC.md`` §12.1.
"""

from __future__ import annotations

import sys
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BeforeValidator, Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[2]
REPO_ROOT = BACKEND_DIR.parent

PLACEHOLDER_SECRET = "CHANGE_ME_generate_a_long_random_value"  # noqa: S105 - sentinel, not a secret


def _split_csv(value: object) -> object:
    """Accept ``"a,b,c"`` as well as a real list, for env-var friendliness."""
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    return value


# ``NoDecode`` is load-bearing: without it pydantic-settings tries to JSON-decode
# any list-typed field straight from the env source, before validators run, so
# ``SUPPORTED_LOCALES=he,en`` raises a JSONDecodeError instead of reaching
# ``_split_csv``.
CsvList = Annotated[list[str], NoDecode, BeforeValidator(_split_csv)]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        # Look for .env at the repo root first (Docker mounts it there too),
        # then inside backend/ for anyone running uvicorn from that directory.
        env_file=(REPO_ROOT / ".env", BACKEND_DIR / ".env"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── application ───────────────────────────────────────────────────────
    APP_NAME: str = "Kavim"
    APP_ENV: Literal["development", "staging", "production"] = "development"
    APP_DEBUG: bool = False
    APP_BASE_URL: str = "http://localhost:5173"
    API_PREFIX: str = "/api/v1"
    LOG_LEVEL: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    LOG_JSON: bool = True

    # ── security ──────────────────────────────────────────────────────────
    SECRET_KEY: SecretStr = SecretStr(PLACEHOLDER_SECRET)
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_TTL_MINUTES: int = 15
    REFRESH_TOKEN_TTL_DAYS: int = 30
    OTP_TTL_MINUTES: int = 10
    OTP_MAX_ATTEMPTS: int = 5
    OTP_REQUEST_LIMIT_PER_15MIN: int = 3
    INVITATION_TTL_DAYS: int = 7
    PASSWORD_RESET_TTL_MINUTES: int = 60
    PASSWORD_MIN_LENGTH: int = 10
    LOGIN_MAX_ATTEMPTS: int = 10
    LOGIN_LOCKOUT_MINUTES: int = 15

    # ── database ──────────────────────────────────────────────────────────
    DATABASE_URL: str = "postgresql+asyncpg://kavim:kavim_dev_password@localhost:5432/kavim"
    DATABASE_POOL_SIZE: int = 20
    DATABASE_MAX_OVERFLOW: int = 10
    DATABASE_ECHO: bool = False

    # ── redis / celery ────────────────────────────────────────────────────
    REDIS_URL: str = "redis://localhost:6379/0"
    CELERY_BROKER_URL: str = "redis://localhost:6379/1"
    CELERY_RESULT_BACKEND: str = "redis://localhost:6379/2"

    # ── localization ──────────────────────────────────────────────────────
    DEFAULT_LOCALE: str = "he"
    SUPPORTED_LOCALES: CsvList = Field(default_factory=lambda: ["he", "en"])
    DEFAULT_TIMEZONE: str = "Asia/Jerusalem"

    # ── CORS (development only; production is single-origin) ──────────────
    CORS_ORIGINS: CsvList = Field(default_factory=list)

    # ── SendGrid ──────────────────────────────────────────────────────────
    SENDGRID_ENABLED: bool = False
    SENDGRID_SANDBOX: bool = True
    SENDGRID_API_KEY: SecretStr = SecretStr("")
    SENDGRID_FROM_EMAIL: str = "no-reply@example.com"
    SENDGRID_FROM_NAME: str = "Kavim"
    SENDGRID_WEBHOOK_KEY: SecretStr = SecretStr("")
    SENDGRID_TEMPLATE_INVITATION: str = ""
    SENDGRID_TEMPLATE_OTP: str = ""
    SENDGRID_TEMPLATE_PASSWORD_RESET: str = ""
    SENDGRID_TEMPLATE_TASK_ASSIGNED: str = ""
    SENDGRID_TEMPLATE_MENTION: str = ""
    SENDGRID_TEMPLATE_OVERDUE: str = ""
    SENDGRID_TEMPLATE_DIGEST: str = ""

    # ── Twilio ────────────────────────────────────────────────────────────
    TWILIO_ENABLED: bool = False
    TWILIO_ACCOUNT_SID: str = ""
    TWILIO_AUTH_TOKEN: SecretStr = SecretStr("")
    TWILIO_FROM_NUMBER: str = ""
    TWILIO_STATUS_CALLBACK_URL: str = ""

    # ── storage ───────────────────────────────────────────────────────────
    STORAGE_BACKEND: Literal["local", "s3"] = "local"
    STORAGE_LOCAL_PATH: str = "./.storage"
    STORAGE_BUCKET: str = ""
    STORAGE_ENDPOINT: str = ""
    STORAGE_REGION: str = ""
    STORAGE_ACCESS_KEY: SecretStr = SecretStr("")
    STORAGE_SECRET_KEY: SecretStr = SecretStr("")
    STORAGE_PRESIGN_TTL_SECONDS: int = 900
    UPLOAD_MAX_BYTES: int = 25 * 1024 * 1024

    # ── observability ─────────────────────────────────────────────────────
    SENTRY_DSN: str = ""

    # ── derived ───────────────────────────────────────────────────────────
    @property
    def is_production(self) -> bool:
        return self.APP_ENV == "production"

    @property
    def is_development(self) -> bool:
        return self.APP_ENV == "development"

    @property
    def storage_local_dir(self) -> Path:
        path = Path(self.STORAGE_LOCAL_PATH)
        return path if path.is_absolute() else (BACKEND_DIR / path).resolve()

    @model_validator(mode="after")
    def _guard_production(self) -> Settings:
        """Refuse to start production with development-grade settings.

        These are the mistakes that are silent in staging and expensive in
        production, so they fail loudly instead.
        """
        if not self.is_production:
            return self

        problems: list[str] = []

        if self.SECRET_KEY.get_secret_value() == PLACEHOLDER_SECRET:
            problems.append("SECRET_KEY is still the placeholder value")
        if len(self.SECRET_KEY.get_secret_value()) < 32:
            problems.append("SECRET_KEY must be at least 32 characters")
        if self.APP_DEBUG:
            problems.append("APP_DEBUG must be false in production")
        if self.DATABASE_ECHO:
            problems.append("DATABASE_ECHO must be false in production (it logs query values)")
        if self.APP_BASE_URL.startswith("http://"):
            problems.append("APP_BASE_URL must use https in production")
        if self.SENDGRID_ENABLED and self.SENDGRID_SANDBOX:
            problems.append("SENDGRID_SANDBOX must be false in production — no mail is sent")
        if self.STORAGE_BACKEND == "local":
            problems.append("STORAGE_BACKEND=local is not durable; use s3 in production")

        if problems:
            raise ValueError("invalid production configuration:\n  - " + "\n  - ".join(problems))
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached settings accessor. Import this, not the class."""
    try:
        return Settings()
    except Exception as exc:  # pragma: no cover - startup path
        # A config error must be readable at a glance, not buried in a traceback.
        print(f"\n[kavim] configuration error:\n{exc}\n", file=sys.stderr)  # noqa: T201
        raise


settings = get_settings()
