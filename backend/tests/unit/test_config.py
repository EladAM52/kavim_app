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
        "SENDGRID_ENABLED": False,
        "SENDGRID_SANDBOX": True,
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
        ({"SENDGRID_ENABLED": True, "SENDGRID_SANDBOX": True}, "SANDBOX"),
    ],
)
def test_production_rejects_unsafe_config(
    overrides: dict[str, object], expected_fragment: str
) -> None:
    with pytest.raises(ValueError, match=expected_fragment):
        Settings(**_prod(**overrides))  # type: ignore[arg-type]


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
