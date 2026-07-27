"""The permission model, asserted without a database (SPEC §8.4).

Everything in `core/permissions.py` is pure data and pure functions, which is the
whole reason it was kept free of `models` and `Depends`. These tests are the
payoff: the authorization *rules* can be pinned here, in milliseconds, leaving the
integration suite to prove only that the rules are actually consulted.

The column cases matter most. They assert FR-205 — "a worker must not sign off
their own fix" — against the real seeded board, and they do it in Phase 3, before
the cell-write endpoint that enforces them exists. When Phase 5 writes that
endpoint, the rule it calls is already correct and already covered.
"""

from __future__ import annotations

import pytest

from app.core.enums import ProjectPermissionLevel, RoleKey
from app.core.permissions import (
    DEFAULT_ROLE_MATRIX,
    PERMISSION_KEYS,
    PERMISSIONS,
    PROJECT_LEVEL_PERMISSIONS,
    ROLE_LABELS,
    ROLE_RANKS,
    column_is_editable,
    resolve_effective_permissions,
)
from app.scripts.seed import DEMO_COLUMNS


# ══════════════════════════════════════════════════════════════════════════
#  the registry
# ══════════════════════════════════════════════════════════════════════════
def test_every_permission_key_is_unique() -> None:
    keys = [spec.key for spec in PERMISSIONS]
    assert len(keys) == len(set(keys))


def test_every_permission_key_follows_the_naming_convention() -> None:
    """`resource:action[:qualifier]` — CLAUDE.md's conventions table."""
    for spec in PERMISSIONS:
        segments = spec.key.split(":")
        assert 2 <= len(segments) <= 3, spec.key
        assert all(segment.islower() and segment for segment in segments), spec.key


def test_the_resource_field_is_a_ui_grouping_not_the_key_prefix() -> None:
    """`spec.resource` groups the RoleMatrix; it is not derived from the key.

    `column:manage`, `group:manage`, and `template:manage` all carry
    `resource="structure"` so they appear under one heading rather than as three
    single-row sections. Asserted because the two look interchangeable and a
    later "cleanup" that derives `resource` from the key prefix would silently
    fragment that group.
    """
    structure = {spec.key for spec in PERMISSIONS if spec.resource == "structure"}
    assert structure == {"column:manage", "group:manage", "template:manage"}

    # Every resource is a usable heading, and the grouping covers the registry.
    assert all(spec.resource and spec.resource.islower() for spec in PERMISSIONS)
    assert sum(1 for _ in PERMISSIONS) == len(PERMISSION_KEYS)


def test_every_permission_is_described_in_both_locales() -> None:
    # A blank Hebrew description renders as an empty row in the RoleMatrix, which
    # reads as a bug rather than as missing copy.
    for spec in PERMISSIONS:
        assert spec.description_he.strip()
        assert spec.description_en.strip()


# ══════════════════════════════════════════════════════════════════════════
#  the seeded role matrix
# ══════════════════════════════════════════════════════════════════════════
def test_the_default_matrix_only_grants_registry_keys() -> None:
    """A permission granted but never registered can never be required.

    It would sit in `role_permissions` forever, invisible to the admin UI (which
    lists the registry) and unmatched by any route.
    """
    for role, granted in DEFAULT_ROLE_MATRIX.items():
        assert granted <= PERMISSION_KEYS, f"{role}: {sorted(granted - PERMISSION_KEYS)}"


def test_every_role_appears_in_the_matrix_labels_and_ranks() -> None:
    for role in RoleKey:
        assert role in DEFAULT_ROLE_MATRIX
        assert role in ROLE_LABELS
        assert role in ROLE_RANKS


def test_only_system_admin_holds_user_manage_permissions() -> None:
    """The delegation invariant, asserted rather than left as a comment.

    `user:manage_permissions` is the permission that grants permissions. Anyone
    holding it can grant themselves everything else, so it is the one entry in the
    matrix whose default must not drift.
    """
    holders = {
        role
        for role, granted in DEFAULT_ROLE_MATRIX.items()
        if "user:manage_permissions" in granted
    }
    assert holders == {RoleKey.SYSTEM_ADMIN}


def test_system_admin_holds_everything() -> None:
    assert DEFAULT_ROLE_MATRIX[RoleKey.SYSTEM_ADMIN] == PERMISSION_KEYS


def test_role_ranks_are_a_strict_total_order() -> None:
    # Ties would make the admin matrix ordering and the FR-708 escalation target
    # non-deterministic.
    ranks = list(ROLE_RANKS.values())
    assert len(ranks) == len(set(ranks))
    assert ROLE_RANKS[RoleKey.SYSTEM_ADMIN] < ROLE_RANKS[RoleKey.WORKER]


def test_seniority_does_not_imply_a_superset() -> None:
    """VIEWER outranks nobody yet holds `audit:read`, which WORKER does not.

    Recorded as a test because it looks like a bug on first reading and is not:
    the auditor is a compliance role, not a junior one. Anything that later
    "fixes" the matrix into a clean hierarchy has to delete this test first.
    """
    assert "audit:read" in DEFAULT_ROLE_MATRIX[RoleKey.VIEWER]
    assert "audit:read" not in DEFAULT_ROLE_MATRIX[RoleKey.WORKER]
    assert ROLE_RANKS[RoleKey.VIEWER] > ROLE_RANKS[RoleKey.WORKER]


# ══════════════════════════════════════════════════════════════════════════
#  layer 1 ∩ layer 2
# ══════════════════════════════════════════════════════════════════════════
def test_a_non_member_gets_nothing() -> None:
    """`project_level=None` is the whole point of layer 2.

    A Line Manager holds `task:update:any` globally and still must not touch a
    board they were never added to.
    """
    manager = DEFAULT_ROLE_MATRIX[RoleKey.LINE_MANAGER]
    assert resolve_effective_permissions(manager, None) == frozenset()


@pytest.mark.parametrize("role", list(RoleKey))
@pytest.mark.parametrize("level", list(ProjectPermissionLevel))
def test_a_project_level_can_only_narrow_never_widen(
    role: RoleKey, level: ProjectPermissionLevel
) -> None:
    """Membership is a filter, not a grant.

    If any level could add a permission the global role lacks, project sharing
    would become a privilege-escalation path — a manager could hand out `OWNER` on
    a board and silently promote a worker.
    """
    granted = DEFAULT_ROLE_MATRIX[role]
    assert resolve_effective_permissions(granted, level) <= granted


def test_owner_level_narrows_nothing() -> None:
    granted = DEFAULT_ROLE_MATRIX[RoleKey.SHIFT_SUPERVISOR]
    assert resolve_effective_permissions(granted, ProjectPermissionLevel.OWNER) == granted


def test_viewer_level_strips_every_write() -> None:
    effective = resolve_effective_permissions(
        DEFAULT_ROLE_MATRIX[RoleKey.LINE_MANAGER], ProjectPermissionLevel.VIEWER
    )
    assert "task:update:any" not in effective
    assert "comment:create" not in effective
    assert "project:read" in effective


def test_every_project_level_is_defined() -> None:
    for level in ProjectPermissionLevel:
        assert level in PROJECT_LEVEL_PERMISSIONS


# ══════════════════════════════════════════════════════════════════════════
#  layer 3 — columns (FR-205)
# ══════════════════════════════════════════════════════════════════════════
def test_an_unrestricted_column_is_editable_by_anyone() -> None:
    """Empty means "no column-level rule", not "nobody".

    Getting this backwards would make every newly created column read-only to
    everyone, which reads as data loss rather than as a permission bug.
    """
    assert column_is_editable([], [RoleKey.WORKER]) is True
    assert column_is_editable([], []) is True


def test_a_restricted_column_rejects_a_role_not_on_the_list() -> None:
    assert column_is_editable([RoleKey.LINE_MANAGER], [RoleKey.WORKER]) is False


def test_one_matching_role_is_enough() -> None:
    # A user may hold several roles; any one of them on the list opens the column.
    held = [RoleKey.WORKER, RoleKey.LINE_MANAGER]
    assert column_is_editable([RoleKey.LINE_MANAGER], held) is True


def test_a_user_with_no_roles_cannot_write_a_restricted_column() -> None:
    assert column_is_editable([RoleKey.LINE_MANAGER], []) is False


# The seeded demo board, which is what a reviewer actually looks at. Splitting
# the expectation out by name means a change to the seed shows up here as a named
# failing case rather than as a count mismatch.
_WORKER_EDITABLE = {
    "status",
    "due_date",
    "station",
    "measured_temp",
    "corrective_action",
    "evidence",
}
_MANAGER_ONLY = {"verified", "root_cause_approved_by"}


@pytest.mark.parametrize("spec", DEMO_COLUMNS, ids=lambda spec: str(spec["key"]))
def test_worker_column_access_on_the_seeded_board(spec: dict[str, object]) -> None:
    roles = [str(role) for role in spec["editable_by_roles"]]  # type: ignore[union-attr]
    expected = spec["key"] in _WORKER_EDITABLE
    assert column_is_editable(roles, [RoleKey.WORKER]) is expected


def test_a_worker_cannot_sign_off_their_own_fix() -> None:
    """FR-205's actual reason for existing, stated once in plain terms.

    The worker who wrote the corrective action must not be the one who ticks
    `verified` or names who approved the root cause. That asymmetry is the entire
    argument for having a column layer at all — without it, layers 1 and 2 would
    be enough.
    """
    by_key = {str(spec["key"]): spec for spec in DEMO_COLUMNS}

    for key in _MANAGER_ONLY:
        roles = [str(role) for role in by_key[key]["editable_by_roles"]]  # type: ignore[union-attr]
        assert column_is_editable(roles, [RoleKey.WORKER]) is False, key
        assert column_is_editable(roles, [RoleKey.LINE_MANAGER]) is True, key

    corrective = [str(role) for role in by_key["corrective_action"]["editable_by_roles"]]  # type: ignore[union-attr]
    assert column_is_editable(corrective, [RoleKey.WORKER]) is True


def test_the_line_manager_can_edit_every_seeded_column() -> None:
    # Manager-only columns exist; manager-excluded columns must not.
    for spec in DEMO_COLUMNS:
        roles = [str(role) for role in spec["editable_by_roles"]]  # type: ignore[union-attr]
        assert column_is_editable(roles, [RoleKey.LINE_MANAGER]) is True, spec["key"]


def test_every_seeded_column_names_only_real_roles() -> None:
    """A typo'd role key in the seed silently locks everyone out of that column.

    `editable_by_roles` is a plain `varchar[]` with no foreign key, so nothing in
    the database catches `LINE_MANGER` — the column would simply never match a
    caller and would look like a permission bug in the UI.
    """
    valid = {str(role) for role in RoleKey}
    for spec in DEMO_COLUMNS:
        roles = {str(role) for role in spec["editable_by_roles"]}  # type: ignore[union-attr]
        assert roles <= valid, f"{spec['key']}: {sorted(roles - valid)}"
