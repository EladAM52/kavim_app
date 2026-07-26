"""SQLAlchemy ORM models, one module per aggregate.

**Every model must be imported here.** Alembic autogenerate only sees tables
registered on `Base.metadata`, and a model that is never imported is never
registered — so a missing import silently produces a migration that drops the
table. Importing them all in one place makes that impossible.

May import only ``app.core``.
"""

from app.models.attachment import Attachment
from app.models.audit import AuditLog
from app.models.auth import Invitation, OtpCode, PasswordResetToken, RefreshToken
from app.models.base import Base
from app.models.column import BoardColumn
from app.models.comment import Comment
from app.models.notification import (
    InAppNotification,
    NotificationDelivery,
    NotificationOutbox,
)
from app.models.project import Group, Project, ProjectMember, SavedView
from app.models.role import Permission, Role, RolePermission, UserRole
from app.models.site import Line, Site
from app.models.task import Task, TaskAssignee, TaskCellHistory, TaskDependency
from app.models.user import NotificationPreference, User

__all__ = [
    "Attachment",
    "AuditLog",
    "Base",
    "BoardColumn",
    "Comment",
    "Group",
    "InAppNotification",
    "Invitation",
    "Line",
    "NotificationDelivery",
    "NotificationOutbox",
    "NotificationPreference",
    "OtpCode",
    "PasswordResetToken",
    "Permission",
    "Project",
    "ProjectMember",
    "RefreshToken",
    "Role",
    "RolePermission",
    "SavedView",
    "Site",
    "Task",
    "TaskAssignee",
    "TaskCellHistory",
    "TaskDependency",
    "User",
    "UserRole",
]
