"""Shared foundation: config, database, logging, errors, security.

Nothing in ``app.core`` may import from ``app.modules``, ``app.schemas``, or
``app.integrations`` — enforced by ``.importlinter``.
"""
