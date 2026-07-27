"""External provider adapters (SPEC §6.14).

The only place ``aiosmtplib``, ``smtplib``, or ``boto3`` may be imported. Keeps
the application portable and gives tests a single seam to stub.

Email specifically is two files rather than one: ``email.py`` holds the
``EmailSender`` protocol and the provider-neutral message type, and
``smtp_client.py`` holds the Gmail implementation. ``modules/notifications``
depends only on the former, so swapping providers — the only way to regain real
delivery tracking (ADR-007) — stays a one-file change.
"""
