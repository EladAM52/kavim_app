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

    # ── email / SMTP (ADR-007) ────────────────────────────────────────────
    # Gmail via authenticated SMTP. SMTP_PASSWORD is a Google **App Password**
    # (16 characters, 2-step verification required on the account), never the
    # account password.
    EMAIL_ENABLED: bool = False
    EMAIL_DRY_RUN: bool = True
    SMTP_HOST: str = "smtp.gmail.com"
    SMTP_PORT: int = 587
    SMTP_USERNAME: str = ""
    SMTP_PASSWORD: SecretStr = SecretStr("")
    SMTP_STARTTLS: bool = True
    SMTP_USE_TLS: bool = False
    SMTP_TIMEOUT_SECONDS: int = 30
    EMAIL_FROM_ADDRESS: str = ""
    EMAIL_FROM_NAME: str = "Kavim"
    EMAIL_REPLY_TO: str = ""
    # Free Gmail sends ~500 recipients/day; Workspace ~2000. Exceeding it
    # suspends sending for 24 hours, so the outbox meters against this (FR-714).
    EMAIL_DAILY_QUOTA: int = 500

    # ── SMS ───────────────────────────────────────────────────────────────
    # Deferred (SPEC §6.14.1). No provider, no settings. NotificationChannel.SMS
    # and the schema stay put, so adding one later needs no migration.

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

    @property
    def email_from_address(self) -> str:
        """The envelope sender.

        Defaults to ``SMTP_USERNAME`` because on free Gmail the two must match:
        Gmail rewrites a ``From`` it has not authorized, so a mismatch does not
        error — it silently sends from the wrong address, which is worse.
        """
        return self.EMAIL_FROM_ADDRESS or self.SMTP_USERNAME

    @property
    def is_gmail_smtp(self) -> bool:
        return self.SMTP_HOST.endswith("gmail.com")

    @model_validator(mode="after")
    def _guard_email(self) -> Settings:
        """Email settings that are wrong in *every* environment.

        Checked unconditionally, unlike the production guard below, because a
        misconfigured transport fails at first send — which for OTP means at the
        moment a user is locked out, not at startup.
        """
        if self.SMTP_STARTTLS and self.SMTP_USE_TLS:
            raise ValueError(
                "SMTP_STARTTLS and SMTP_USE_TLS are mutually exclusive: "
                "use STARTTLS on port 587 or implicit TLS on port 465, not both"
            )
        if self.EMAIL_ENABLED and not self.EMAIL_DRY_RUN:
            if not self.SMTP_USERNAME:
                raise ValueError("SMTP_USERNAME is required when EMAIL_ENABLED and not dry-run")
            if not self.SMTP_PASSWORD.get_secret_value():
                raise ValueError(
                    "SMTP_PASSWORD is required when EMAIL_ENABLED and not dry-run "
                    "(Gmail: a 16-character App Password, not the account password)"
                )
            # Free Gmail rewrites an unauthorized From rather than rejecting it.
            if self.is_gmail_smtp and self.email_from_address != self.SMTP_USERNAME:
                raise ValueError(
                    f"EMAIL_FROM_ADDRESS ({self.email_from_address}) must equal SMTP_USERNAME "
                    f"({self.SMTP_USERNAME}) on Gmail — Gmail silently rewrites any other sender"
                )
        return self

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
        if self.EMAIL_DRY_RUN:
            problems.append("EMAIL_DRY_RUN must be false in production — no mail is sent")
        if not self.EMAIL_ENABLED:
            problems.append(
                "EMAIL_ENABLED must be true in production — invitations and OTP codes "
                "cannot be delivered otherwise"
            )
        if not (self.SMTP_STARTTLS or self.SMTP_USE_TLS):
            problems.append("SMTP must use STARTTLS or implicit TLS; plaintext submission")
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
