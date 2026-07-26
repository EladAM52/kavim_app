"""Feature modules (SPEC §6.2–6.12).

Modules communicate through each other's ``service.py`` functions only — never
by importing another module's ``router.py``. Enforced by ``.importlinter``.
"""
