"""Self-service profile (SPEC §6.3).

Phase 3 ships `GET /users/me` and `PATCH /users/me`. The rest of the service
arrives with its dependencies: `POST /users/me/avatar` needs object storage
(Phase 6), the notification-preference pair needs the preference matrix (Phase 7),
and `GET /users` — the member picker — needs projects to scope by, since SPEC §6.3
requires it to return only users sharing a project with the caller rather than a
plant-wide address book.
"""
