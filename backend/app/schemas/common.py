"""Schema pieces shared by every module (SPEC §9.1).

`schemas/auth.py` grew these first and re-exports them from here, so no existing
import or generated frontend type changes. New modules import from this file.
"""

from __future__ import annotations

import base64
import binascii
import json
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict

from app.core.exceptions import BadRequestError


class SchemaBase(BaseModel):
    """`extra="forbid"` is a security property, not tidiness.

    Without it a client can send `{"email": ...}` to an endpoint that does not
    accept one and have it silently ignored — which reads to the caller as
    accepted. Rejecting the unknown field turns a misunderstanding into a 422 at
    the boundary instead of a wrong assumption downstream.
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


# ══════════════════════════════════════════════════════════════════════════
#  keyset pagination
# ══════════════════════════════════════════════════════════════════════════
class Page[T](SchemaBase):
    """One page of a list, plus the cursor for the next.

    **No `total`.** SPEC §9.1 rules out `OFFSET` because it degrades and skips
    rows under concurrent insertion, and a total count brings the same problem
    back by the side door: `COUNT(*)` over a filtered audit scan is the expensive
    half of the query, and it is stale the moment it is computed. A UI that needs
    "is there more?" has `next_cursor`.
    """

    items: list[T]
    next_cursor: str | None = None


# ══════════════════════════════════════════════════════════════════════════
#  generic responses
# ══════════════════════════════════════════════════════════════════════════
class AcceptedResponse(SchemaBase):
    """A `202` for endpoints that must not reveal whether anything happened.

    OTP request and password reset both return this whether or not the address
    exists, because a different response for a known address is a user
    enumeration oracle (SPEC §8.3).
    """

    status: str = "accepted"
    detail: str | None = None


class MessageResponse(SchemaBase):
    detail: str


# ══════════════════════════════════════════════════════════════════════════
#  field normalisation
# ══════════════════════════════════════════════════════════════════════════
def normalize_israeli_phone(value: str | None) -> str | None:
    """Store E.164, always.

    `050-123-4567` and `+972501234567` are the same number, and keeping both
    shapes makes every later comparison — deduplication, SMS lookup, "is this the
    person who reported it" — quietly unreliable.

    Shared by registration and profile editing so the two cannot normalise
    differently, which would let a user's number change shape by being re-saved.
    """
    if not value:
        return None
    digits = "".join(char for char in value if char.isdigit() or char == "+")
    if digits.startswith("0"):
        return f"+972{digits[1:]}"
    if not digits.startswith("+"):
        return f"+{digits}"
    return digits


def encode_cursor(payload: dict[str, Any]) -> str:
    """Opaque cursor from the sort key of the last row on a page.

    Base64 rather than a bare id so that clients cannot construct one by guessing
    and do not come to depend on its shape — the sort key changes when the sort
    changes, and a client that parsed it would break silently.
    """
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True, default=str)
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


def decode_cursor(cursor: str) -> dict[str, Any]:
    """Reverse of `encode_cursor`. A malformed cursor is a 400, never a 500.

    Cursors travel in URLs, get truncated by chat clients, and get hand-edited.
    None of that is a server fault, so none of it should page anybody.
    """
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        decoded = json.loads(base64.urlsafe_b64decode(padded.encode()).decode())
    except (binascii.Error, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise BadRequestError("That page cursor is not valid.") from exc

    if not isinstance(decoded, dict):
        raise BadRequestError("That page cursor is not valid.")
    return decoded


def cursor_datetime(payload: dict[str, Any], key: str) -> datetime:
    """Read a timestamp back out of a cursor as a real `datetime`.

    `encode_cursor` serialises with `default=str`, so the value comes back as an
    ISO string. Handing that straight to a `WHERE (created_at, id) < (...)`
    comparison makes PostgreSQL compare `timestamptz` against `text`, which is
    not a silent coercion — it is `operator does not exist`, a 500 on page two.
    """
    try:
        return datetime.fromisoformat(str(payload[key]))
    except (KeyError, TypeError, ValueError) as exc:
        raise BadRequestError("That page cursor is not valid.") from exc
