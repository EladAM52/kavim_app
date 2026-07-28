"""Configuration guardrails.

The production validator exists so a misconfigured deploy fails loudly at
startup instead of quietly running with development-grade settings.
"""

from __future__ import annotations

import pytest

from app.core.config import PLACEHOLDER_SECRET, Settings


def _prod(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "APP_ENV": "production",
        "APP_DEBUG": False,
        "DATABASE_ECHO": False,
        "APP_BASE_URL": "https://kavim.example.com",
        "SECRET_KEY": "x" * 48,
        "STORAGE_BACKEND": "s3",
        "EMAIL_ENABLED": True,
        "EMAIL_DRY_RUN": False,
        "SMTP_USERNAME": "kavimsupport@gmail.com",
        "SMTP_PASSWORD": "app-password-16ch",
        "EMAIL_FROM_ADDRESS": "kavimsupport@gmail.com",
        # Ignore any .env present on the machine running the tests.
        "_env_file": None,
    }
    base.update(overrides)
    return base


def test_valid_production_config_is_accepted() -> None:
    settings = Settings(**_prod())  # type: ignore[arg-type]
    assert settings.is_production
    assert not settings.is_development


@pytest.mark.parametrize(
    ("overrides", "expected_fragment"),
    [
        ({"SECRET_KEY": PLACEHOLDER_SECRET}, "placeholder"),
        ({"SECRET_KEY": "tooshort"}, "at least 32"),
        ({"APP_DEBUG": True}, "APP_DEBUG"),
        ({"DATABASE_ECHO": True}, "DATABASE_ECHO"),
        ({"APP_BASE_URL": "http://kavim.example.com"}, "https"),
        ({"STORAGE_BACKEND": "local"}, "not durable"),
        ({"EMAIL_DRY_RUN": True}, "EMAIL_DRY_RUN"),
        ({"EMAIL_ENABLED": False}, "EMAIL_ENABLED"),
        ({"SMTP_STARTTLS": False, "SMTP_USE_TLS": False}, "plaintext"),
    ],
)
def test_production_rejects_unsafe_config(
    overrides: dict[str, object], expected_fragment: str
) -> None:
    with pytest.raises(ValueError, match=expected_fragment):
        Settings(**_prod(**overrides))  # type: ignore[arg-type]


# ══════════════════════════════════════════════════════════════════════════
#  email transport — checked in every environment, not just production
# ══════════════════════════════════════════════════════════════════════════
def test_starttls_and_implicit_tls_are_mutually_exclusive() -> None:
    with pytest.raises(ValueError, match="mutually exclusive"):
        Settings(SMTP_STARTTLS=True, SMTP_USE_TLS=True, _env_file=None)  # type: ignore[call-arg]


def test_sending_without_a_password_is_rejected() -> None:
    """Caught at startup rather than at the first OTP send, which is the moment
    a user is already locked out and waiting."""
    with pytest.raises(ValueError, match="SMTP_PASSWORD"):
        Settings(  # type: ignore[call-arg]
            EMAIL_ENABLED=True,
            EMAIL_DRY_RUN=False,
            SMTP_USERNAME="kavimsupport@gmail.com",
            SMTP_PASSWORD="",
            _env_file=None,
        )


def test_gmail_rejects_a_from_address_it_would_rewrite() -> None:
    """Gmail does not error on an unauthorized From — it silently substitutes
    its own, so the mismatch has to be caught here or not at all."""
    with pytest.raises(ValueError, match="silently rewrites"):
        Settings(  # type: ignore[call-arg]
            EMAIL_ENABLED=True,
            EMAIL_DRY_RUN=False,
            SMTP_USERNAME="kavimsupport@gmail.com",
            SMTP_PASSWORD="app-password-16ch",
            EMAIL_FROM_ADDRESS="no-reply@kavim.local",
            _env_file=None,
        )


def test_from_address_falls_back_to_the_smtp_username() -> None:
    settings = Settings(SMTP_USERNAME="kavimsupport@gmail.com", _env_file=None)  # type: ignore[call-arg]
    assert settings.email_from_address == "kavimsupport@gmail.com"


def test_dry_run_needs_no_credentials() -> None:
    """The development default: no App Password, no connection, no friction."""
    settings = Settings(EMAIL_ENABLED=True, EMAIL_DRY_RUN=True, _env_file=None)  # type: ignore[call-arg]
    assert settings.EMAIL_DRY_RUN


def test_development_allows_placeholder_secret() -> None:
    """Development must stay frictionless — the guard is production-only."""
    settings = Settings(APP_ENV="development", SECRET_KEY=PLACEHOLDER_SECRET, _env_file=None)  # type: ignore[call-arg]
    assert settings.is_development


def test_csv_init_values_are_split() -> None:
    settings = Settings(
        SUPPORTED_LOCALES="he, en, ar",  # type: ignore[arg-type]
        CORS_ORIGINS="http://a.test,http://b.test",  # type: ignore[arg-type]
        _env_file=None,  # type: ignore[call-arg]
    )
    assert settings.SUPPORTED_LOCALES == ["he", "en", "ar"]
    assert settings.CORS_ORIGINS == ["http://a.test", "http://b.test"]


def test_csv_env_vars_are_split(monkeypatch: pytest.MonkeyPatch) -> None:
    """Regression: comma-separated lists must parse from the environment.

    pydantic-settings JSON-decodes list-typed fields at the source level, before
    validators run, so a bare ``he,en`` raises JSONDecodeError unless the field
    is annotated ``NoDecode``. Passing values as init kwargs does not exercise
    that path — only real env vars do.
    """
    monkeypatch.setenv("SUPPORTED_LOCALES", "he,en,ar")
    monkeypatch.setenv("CORS_ORIGINS", "http://a.test, http://b.test")

    settings = Settings(_env_file=None)  # type: ignore[call-arg]

    assert settings.SUPPORTED_LOCALES == ["he", "en", "ar"]
    assert settings.CORS_ORIGINS == ["http://a.test", "http://b.test"]


def test_empty_csv_env_var_yields_empty_list(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CORS_ORIGINS", "")
    assert Settings(_env_file=None).CORS_ORIGINS == []  # type: ignore[call-arg]


# ══════════════════════════════════════════════════════════════════════════
#  subpath deployment (APP_PUBLIC_PATH)
# ══════════════════════════════════════════════════════════════════════════
def test_the_refresh_cookie_path_is_the_api_prefix_at_a_host_root() -> None:
    settings = Settings(_env_file=None)  # type: ignore[arg-type]
    assert settings.refresh_cookie_path == "/api/v1/auth"


def test_the_refresh_cookie_path_carries_the_public_prefix() -> None:
    """The browser matches a cookie path against the address bar.

    nginx strips `/kavim` before the request reaches the app, so the backend
    never sees it — but the browser still does. Without the prefix here the
    cookie is set and then never sent back, which looks like a token bug: login
    succeeds, and the next page load signs the user out.
    """
    settings = Settings(APP_PUBLIC_PATH="/kavim", _env_file=None)  # type: ignore[arg-type]
    assert settings.refresh_cookie_path == "/kavim/api/v1/auth"


@pytest.mark.parametrize("value", ["kavim", "/kavim/", "kavim/"])
def test_a_malformed_public_path_fails_at_startup(value: str) -> None:
    """Each of these produces a cookie path no browser will ever match, and the
    only symptom is users being signed out on reload — so it fails here instead.
    """
    with pytest.raises(ValueError, match="APP_PUBLIC_PATH"):
        Settings(APP_PUBLIC_PATH=value, _env_file=None)  # type: ignore[arg-type]


def test_an_empty_public_path_is_the_root_deployment() -> None:
    settings = Settings(APP_PUBLIC_PATH="", _env_file=None)  # type: ignore[arg-type]
    assert settings.refresh_cookie_path == "/api/v1/auth"
