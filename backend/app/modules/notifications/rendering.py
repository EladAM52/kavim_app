"""Render an outbox payload into a localized `EmailMessage` (ADR-007).

Templates are files in `templates/<event>/`, versioned in git. That is the
deliberate replacement for SendGrid's hosted dynamic templates: copy changes get
reviewed in a pull request, they diff, and they cannot drift between
environments.

Autoescape is on for HTML and **off** for text. Escaping a plain-text body would
turn a Hebrew apostrophe into `&#39;` in someone's inbox.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jinja2 import (
    Environment,
    FileSystemLoader,
    StrictUndefined,
    TemplateNotFound,
    select_autoescape,
)

from app.core.enums import Locale, NotificationEvent
from app.core.logging import get_logger

logger = get_logger(__name__)

TEMPLATE_DIR = Path(__file__).parent / "templates"

# StrictUndefined, not the default silent Undefined. A missing `code` variable
# must fail loudly at render time — the alternative is mailing someone a
# verification message with a blank space where their code should be.
_env = Environment(
    loader=FileSystemLoader(TEMPLATE_DIR),
    autoescape=select_autoescape(enabled_extensions=("html",), default_for_string=False),
    undefined=StrictUndefined,
    trim_blocks=True,
    lstrip_blocks=True,
)


class TemplateMissingError(Exception):
    """No template for this event and locale.

    Raised rather than falling back to English silently: a Hebrew-speaking worker
    receiving an English OTP mail is a bug someone should see.
    """


@dataclass(frozen=True, slots=True)
class RenderedEmail:
    subject: str
    text_body: str
    html_body: str | None


def _render(name: str, context: dict[str, Any]) -> str:
    return _env.get_template(name).render(**context).strip()


def render_email(
    event: NotificationEvent,
    locale: Locale | str,
    context: dict[str, Any],
) -> RenderedEmail:
    """Render subject, text, and (where present) HTML for one event.

    The HTML part is optional by design — a text-only template is a complete,
    sendable message, so adding an event does not require writing HTML first.
    """
    resolved = Locale(locale) if not isinstance(locale, Locale) else locale
    directory = event.value

    try:
        subject = _render(f"{directory}/subject.{resolved.value}.txt", context)
        text_body = _render(f"{directory}/body.{resolved.value}.txt", context)
    except TemplateNotFound as exc:
        raise TemplateMissingError(
            f"no {resolved.value} template for event {event.value!r} (looked in {TEMPLATE_DIR})"
        ) from exc

    try:
        html_body: str | None = _render(f"{directory}/body.{resolved.value}.html", context)
    except TemplateNotFound:
        html_body = None
        # `notification_event`, not `event` — structlog reserves that keyword for
        # the message itself.
        logger.debug(
            "email_html_template_absent",
            notification_event=event.value,
            locale=resolved.value,
        )

    # A subject spanning lines produces a malformed header, and the templates end
    # with a newline for tidiness — so collapse rather than trusting the file.
    return RenderedEmail(
        subject=" ".join(subject.split()),
        text_body=text_body,
        html_body=html_body,
    )


def available_events() -> set[str]:
    """Event directories that actually have templates.

    Used by the test that asserts every event the code can queue has copy in both
    locales — the failure mode otherwise is discovering it at send time.
    """
    if not TEMPLATE_DIR.is_dir():
        return set()
    return {child.name for child in TEMPLATE_DIR.iterdir() if child.is_dir()}
