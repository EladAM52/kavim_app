"""notification deliveries may precede a user row

Invitation and OTP mail is sent to people who do not have accounts yet — both
precede the `users` row by design (SPEC §8.1). A NOT NULL `recipient_id`
therefore made a delivery record impossible for exactly the mail whose failure is
most costly: a bad invitation address means the user never registers at all, and
without a row nobody can see why.

`destination` carries the address when there is no user to point at. A new CHECK
makes sure at least one of the two is present, because a delivery row that
identifies neither is unauditable and answers no question.

Also adds `deferred_quota` to `delivery_status`. Quota deferral and quiet-hours
deferral both mean "deliberately not sent yet", but they send an admin to
different places to fix it — the Gmail ceiling versus a user's schedule. This is
the `VARCHAR` + `CHECK` payoff over a native `ENUM`: adding a value is a plain
constraint swap that runs inside the transaction, not an `ALTER TYPE`.

Revision ID: 09adde4def09
Revises: f81eb8b34800
Create Date: 2026-07-27 15:10:34.256874+00:00

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "09adde4def09"
down_revision: str | None = "f81eb8b34800"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_DELIVERY_STATUSES = (
    "pending",
    "sent",
    "delivered",
    "bounced",
    "failed",
    "skipped_preference",
    "skipped_opted_out",
    "skipped_unverified",
    "deferred_quiet_hours",
)
_NEW_STATUS = "deferred_quota"


def _status_check(values: Sequence[str]) -> str:
    rendered = ", ".join(f"'{value}'" for value in values)
    return f"status IN ({rendered})"


def upgrade() -> None:
    op.alter_column(
        "notification_deliveries", "recipient_id", existing_type=sa.UUID(), nullable=True
    )
    op.create_check_constraint(
        "recipient_or_destination_present",
        "notification_deliveries",
        "recipient_id IS NOT NULL OR destination IS NOT NULL",
    )

    # Widen delivery_status by swapping its CHECK.
    op.drop_constraint("delivery_status", "notification_deliveries", type_="check")
    op.create_check_constraint(
        "delivery_status",
        "notification_deliveries",
        _status_check([*_DELIVERY_STATUSES, _NEW_STATUS]),
    )


def downgrade() -> None:
    # Rows carrying the new status cannot satisfy the narrower CHECK, so they are
    # relabelled rather than left to block the constraint swap. Quiet hours is the
    # nearest surviving meaning: both say "deliberately not sent yet".
    op.execute(
        f"UPDATE notification_deliveries SET status = 'deferred_quiet_hours' "
        f"WHERE status = '{_NEW_STATUS}'"
    )
    op.drop_constraint("delivery_status", "notification_deliveries", type_="check")
    op.create_check_constraint(
        "delivery_status",
        "notification_deliveries",
        _status_check(_DELIVERY_STATUSES),
    )

    # The **bare** name, not the expanded one. `MetaData`'s naming convention is
    # applied here too, so passing the full `ck_…` name gets it prefixed a second
    # time and truncated — producing a constraint that does not exist and a
    # downgrade that dies halfway. Exactly the failure the convention exists to
    # prevent, arrived at from the other direction.
    op.drop_constraint(
        "recipient_or_destination_present",
        "notification_deliveries",
        type_="check",
    )
    # Rows written for an address with no account cannot be represented under the
    # old NOT NULL column, so they are removed rather than blocking the downgrade
    # halfway through. Destructive, and deliberately so: a downgrade that fails
    # partway is worse than one that states its cost. These are delivery receipts,
    # not domain data — the audit log is the durable record.
    op.execute("DELETE FROM notification_deliveries WHERE recipient_id IS NULL")
    op.alter_column(
        "notification_deliveries", "recipient_id", existing_type=sa.UUID(), nullable=False
    )
