"""Append-only audit trail (SPEC §6.11, CLAUDE.md rule 6).

Phase 2 ships the write path. The filtered read endpoint lands in Phase 3 with the
admin panel, behind `audit:read`.
"""
