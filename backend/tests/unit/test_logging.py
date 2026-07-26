"""Logging configuration.

Regression coverage: an incompatible processor chain (a ``structlog.stdlib.*``
processor over a non-stdlib ``WriteLogger``) does not fail at configure time —
it fails at the first log call, which means application startup. So these tests
configure logging and then actually emit records through both chains.
"""

from __future__ import annotations

import json
import logging

import pytest

from app.core.logging import configure_logging, get_logger, request_id_var, user_id_var


@pytest.fixture(autouse=True)
def _restore_logging() -> None:
    """Reconfigure after each test so ordering never matters."""
    yield  # type: ignore[misc]
    configure_logging()


@pytest.fixture
def at_info(monkeypatch: pytest.MonkeyPatch) -> None:
    """Lower the level to INFO for this test.

    ``conftest`` pins LOG_LEVEL to WARNING so the suite stays quiet, which means
    a test that logs at INFO must opt in or its records are correctly dropped.
    """
    from app.core import logging as log_module

    monkeypatch.setattr(log_module.settings, "LOG_LEVEL", "INFO")


def test_native_logger_emits(at_info: None, capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging()
    get_logger(__name__).info("native_event", answer=42)

    out = capsys.readouterr().out
    assert "native_event" in out
    assert "42" in out


def test_stdlib_logger_emits(capsys: pytest.CaptureFixture[str]) -> None:
    """uvicorn, sqlalchemy, and celery log through stdlib, not structlog."""
    configure_logging()
    logging.getLogger("uvicorn.error").warning("foreign_event")

    out = capsys.readouterr().out
    assert "foreign_event" in out


def test_json_output_is_parseable(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Production emits one JSON object per line, or log aggregation breaks."""
    from app.core import logging as log_module

    monkeypatch.setattr(log_module.settings, "LOG_JSON", True)
    monkeypatch.setattr(log_module.settings, "LOG_LEVEL", "INFO")
    configure_logging()

    get_logger("test.module").info("json_event", count=7)

    line = next(ln for ln in capsys.readouterr().out.splitlines() if "json_event" in ln)
    payload = json.loads(line)
    assert payload["event"] == "json_event"
    assert payload["count"] == 7
    assert payload["level"] == "info"
    assert payload["logger"] == "test.module"
    assert payload["timestamp"].endswith("Z")


def test_request_and_user_ids_are_attached(
    at_info: None, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Correlation is the whole point — a line without a request id is unusable."""
    from app.core import logging as log_module

    monkeypatch.setattr(log_module.settings, "LOG_JSON", True)
    configure_logging()

    rid_token = request_id_var.set("req-abc")
    uid_token = user_id_var.set("user-xyz")
    try:
        get_logger("test").info("correlated")
    finally:
        request_id_var.reset(rid_token)
        user_id_var.reset(uid_token)

    line = next(ln for ln in capsys.readouterr().out.splitlines() if "correlated" in ln)
    payload = json.loads(line)
    assert payload["request_id"] == "req-abc"
    assert payload["user_id"] == "user-xyz"


def test_level_filtering_is_applied(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from app.core import logging as log_module

    monkeypatch.setattr(log_module.settings, "LOG_LEVEL", "WARNING")
    configure_logging()

    logger = get_logger("test")
    logger.debug("suppressed_event")
    logger.warning("kept_event")

    out = capsys.readouterr().out
    assert "suppressed_event" not in out
    assert "kept_event" in out


def test_exception_info_is_rendered(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging()
    try:
        raise ValueError("boom")
    except ValueError:
        get_logger("test").exception("failed_event")

    out = capsys.readouterr().out
    assert "failed_event" in out
    assert "ValueError" in out
    assert "boom" in out
