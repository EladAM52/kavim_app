"""Administration: users, the role matrix, invitations, and the audit log.

Split by aggregate rather than into one `service.py`, mirroring `modules/auth/`.
Four unrelated aggregates in a single service module would be six hundred lines
whose only shared property is the URL prefix.

No other module imports this one — administration is a consumer of `auth`,
`audit`, and `notifications`, never a dependency of them (SPEC §6.4).

**Not here, deliberately:** `GET /admin/notifications/deliveries` and its retry
sibling. They appear under `admin` in SPEC §9.3, but §13 assigns the delivery log
to Phase 7 along with the rest of the notification surface.
"""
