"""Permission registry and the default role matrix (SPEC §8.4).

This module is **data only** in Phase 1 — the `require_permission` FastAPI
dependency and the Redis-cached resolver arrive in Phase 3 with the first
protected routes.

Two layers are defined here:

* `PERMISSIONS` — every permission string the application recognises. Rows in
  the `permissions` table are seeded from this list. A route requiring a
  permission absent from here fails closed, which is the intended direction
  (FR-209).
* `DEFAULT_ROLE_MATRIX` — the seeded starting point for role → permission. It is
  *not* the source of truth at runtime: a manager edits the matrix in the admin
  panel (FR-203), so the database wins after seeding.

Effective permission at runtime is the intersection of three layers:
global role permissions ∩ project membership level ∩ column `editable_by_roles`.
"""

from __future__ import annotations

from typing import Final, NamedTuple

from app.core.enums import ProjectPermissionLevel, RoleKey


class PermissionSpec(NamedTuple):
    key: str
    resource: str
    description_he: str
    description_en: str


# ── the registry ──────────────────────────────────────────────────────────
# Format: resource:action[:qualifier]
PERMISSIONS: Final[tuple[PermissionSpec, ...]] = (
    # projects
    PermissionSpec("project:create", "project", "יצירת סקר איכות", "Create a quality review"),
    PermissionSpec("project:read", "project", "צפייה בסקר", "View a review"),
    PermissionSpec("project:update", "project", "עריכת פרטי סקר", "Edit review details"),
    PermissionSpec("project:delete", "project", "מחיקת סקר", "Delete a review"),
    PermissionSpec("project:archive", "project", "העברת סקר לארכיון", "Archive a review"),
    PermissionSpec("project:share", "project", "שיתוף סקר עם עובדים", "Share a review"),
    # board structure
    PermissionSpec("column:manage", "structure", "ניהול עמודות", "Manage columns"),
    PermissionSpec("group:manage", "structure", "ניהול קבוצות", "Manage groups"),
    PermissionSpec("template:manage", "structure", "ניהול תבניות", "Manage templates"),
    # tasks
    PermissionSpec("task:create", "task", "יצירת משימה", "Create a task"),
    PermissionSpec("task:read", "task", "צפייה במשימות", "View tasks"),
    PermissionSpec("task:update:any", "task", "עריכת כל משימה", "Edit any task"),
    PermissionSpec(
        "task:update:assigned", "task", "עריכת משימות שהוקצו לי", "Edit tasks assigned to me"
    ),
    PermissionSpec("task:update:status", "task", "עדכון סטטוס", "Update status"),
    PermissionSpec("task:delete", "task", "מחיקת משימה", "Delete a task"),
    PermissionSpec("task:assign", "task", "הקצאת עובדים", "Assign workers"),
    PermissionSpec("task:bulk_edit", "task", "עריכה מרובה", "Bulk edit"),
    PermissionSpec("task:reorder", "task", "שינוי סדר משימות", "Reorder tasks"),
    # comments and files
    PermissionSpec("comment:create", "comment", "הוספת תגובה", "Add a comment"),
    PermissionSpec("comment:delete:any", "comment", "מחיקת תגובה של אחר", "Delete any comment"),
    PermissionSpec("file:upload", "file", "העלאת קבצים", "Upload files"),
    PermissionSpec("file:delete:any", "file", "מחיקת קובץ של אחר", "Delete any file"),
    # users and administration
    PermissionSpec("user:invite", "user", "הזמנת משתמשים", "Invite users"),
    PermissionSpec("user:read", "user", "צפייה במשתמשים", "View users"),
    PermissionSpec("user:manage", "user", "ניהול משתמשים", "Manage users"),
    PermissionSpec(
        "user:manage_permissions", "user", "ניהול הרשאות", "Manage roles and permissions"
    ),
    # reporting and audit
    PermissionSpec("audit:read", "audit", "צפייה ביומן פעולות", "View the audit log"),
    PermissionSpec("report:read", "report", "צפייה בדוחות", "View reports"),
    PermissionSpec("report:export", "report", "ייצוא דוחות", "Export reports"),
    PermissionSpec(
        "notification:manage_delivery",
        "notification",
        "ניהול שליחת התראות",
        "Manage notification delivery",
    ),
)

PERMISSION_KEYS: Final[frozenset[str]] = frozenset(spec.key for spec in PERMISSIONS)


# ── seeded defaults ───────────────────────────────────────────────────────
_WORKER_PERMISSIONS: Final[tuple[str, ...]] = (
    "project:read",
    "task:read",
    "task:update:assigned",
    "task:update:status",
    "comment:create",
    "file:upload",
    "user:read",
)

_SUPERVISOR_PERMISSIONS: Final[tuple[str, ...]] = (
    *_WORKER_PERMISSIONS,
    "task:create",
    "task:update:any",
    "task:assign",
    "task:reorder",
    "task:bulk_edit",
    "report:read",
    "report:export",
)

_MANAGER_PERMISSIONS: Final[tuple[str, ...]] = (
    *_SUPERVISOR_PERMISSIONS,
    "project:create",
    "project:update",
    "project:delete",
    "project:archive",
    "project:share",
    "column:manage",
    "group:manage",
    "template:manage",
    "task:delete",
    "comment:delete:any",
    "file:delete:any",
    "user:invite",
)

_VIEWER_PERMISSIONS: Final[tuple[str, ...]] = (
    "project:read",
    "task:read",
    "user:read",
    "report:read",
    "report:export",
    "audit:read",
)

DEFAULT_ROLE_MATRIX: Final[dict[RoleKey, frozenset[str]]] = {
    # The only role holding user:manage_permissions — the ability to grant
    # yourself more access is the one that must not be delegated casually.
    RoleKey.SYSTEM_ADMIN: PERMISSION_KEYS,
    RoleKey.LINE_MANAGER: frozenset(_MANAGER_PERMISSIONS),
    RoleKey.SHIFT_SUPERVISOR: frozenset(_SUPERVISOR_PERMISSIONS),
    RoleKey.WORKER: frozenset(_WORKER_PERMISSIONS),
    RoleKey.VIEWER: frozenset(_VIEWER_PERMISSIONS),
}

ROLE_LABELS: Final[dict[RoleKey, tuple[str, str]]] = {
    RoleKey.SYSTEM_ADMIN: ("מנהל מערכת", "System administrator"),
    RoleKey.LINE_MANAGER: ("מנהל קו", "Line manager"),
    RoleKey.SHIFT_SUPERVISOR: ("אחראי משמרת", "Shift supervisor"),
    RoleKey.WORKER: ("עובד", "Worker"),
    RoleKey.VIEWER: ("צופה / מבקר", "Viewer / auditor"),
}

# Lower rank = more senior. Drives ordering in the admin matrix and picks the
# escalation target for an overdue task (FR-708).
ROLE_RANKS: Final[dict[RoleKey, int]] = {
    RoleKey.SYSTEM_ADMIN: 0,
    RoleKey.LINE_MANAGER: 1,
    RoleKey.SHIFT_SUPERVISOR: 2,
    RoleKey.WORKER: 3,
    RoleKey.VIEWER: 4,
}


# ── layer 2: what each project membership level allows ────────────────────
# Intersected with the global role, so a level can only ever *narrow* access.
PROJECT_LEVEL_PERMISSIONS: Final[dict[ProjectPermissionLevel, frozenset[str]]] = {
    ProjectPermissionLevel.OWNER: PERMISSION_KEYS,
    ProjectPermissionLevel.EDITOR: frozenset(_SUPERVISOR_PERMISSIONS) | {"file:upload"},
    ProjectPermissionLevel.COMMENTER: frozenset(
        {
            "project:read",
            "task:read",
            "task:update:status",
            "task:update:assigned",
            "comment:create",
            "file:upload",
            "user:read",
        }
    ),
    ProjectPermissionLevel.VIEWER: frozenset(
        {"project:read", "task:read", "user:read", "report:read"}
    ),
}


def resolve_effective_permissions(
    role_permissions: frozenset[str] | set[str],
    project_level: ProjectPermissionLevel | None,
) -> frozenset[str]:
    """Intersect layers 1 and 2.

    ``project_level=None`` means the user is not a member of the project, which
    grants nothing — a Line Manager holding ``task:update:any`` globally still
    cannot touch a board they were never added to.
    """
    if project_level is None:
        return frozenset()
    return frozenset(role_permissions) & PROJECT_LEVEL_PERMISSIONS[project_level]
