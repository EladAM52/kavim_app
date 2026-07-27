"""Deny by default, enforced mechanically (FR-209, CLAUDE.md rule 2).

CLAUDE.md promises that "every mutation endpoint declares `require_permission(...)`"
and that this file "fails CI if a route omits its declaration". This is that file.
It is the difference between a rule people intend to follow and a rule they cannot
forget to follow.

Nothing here touches the database or issues a request. It reads the route table.

**No `TestClient`.** `fastapi.testclient` emits a `StarletteDeprecationWarning` on
import and `pyproject.toml` sets `filterwarnings = ["error::DeprecationWarning"]`,
so importing it would fail the suite. `create_app()` is all this needs anyway.

**FastAPI nests included routers.** `app.include_router(...)` does not flatten
routes into `app.routes`; it appends an `_IncludedRouter` whose `original_router`
holds them, and a nested route's `.path` carries only its own router's prefix.
A test that iterated `app.routes` looking for `APIRoute` would therefore find
**zero** routes and pass unconditionally — the worst possible failure for a test
whose entire job is to fail. `test_the_walker_sees_every_published_route` exists
to make that impossible: it cross-checks the walk against the OpenAPI path set,
which FastAPI builds by its own independent traversal.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.dependencies.utils import get_flat_dependant
from fastapi.routing import APIRoute

from app.core.permissions import PERMISSION_KEYS
from app.main import create_app
from app.modules.auth.dependencies import PermissionRequirement

Route = tuple[str, str]  # (method, full path)

MUTATION_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


# ══════════════════════════════════════════════════════════════════════════
#  the allowlists
# ══════════════════════════════════════════════════════════════════════════
# Reachable without authentication. Every entry states why, because the only
# thing standing between this list and a security hole is that adding to it is
# meant to feel uncomfortable.
PUBLIC_ROUTES: frozenset[Route] = frozenset(
    {
        ("GET", "/health/live"),  # liveness probe, hit by the orchestrator
        ("GET", "/health/ready"),  # readiness probe
        ("GET", "/api/v1/"),  # API metadata: version, locales, timezone
        # Possession of a 256-bit invitation token *is* the credential here.
        ("GET", "/api/v1/auth/invitations/{token}"),
        ("POST", "/api/v1/auth/otp/request"),  # the account does not exist yet
        ("POST", "/api/v1/auth/otp/verify"),  # the account does not exist yet
        ("POST", "/api/v1/auth/register"),  # creates the account
        ("POST", "/api/v1/auth/login"),  # issues the credential
        ("POST", "/api/v1/auth/refresh"),  # authenticated by the httpOnly cookie
        ("POST", "/api/v1/auth/logout"),  # must appear to succeed for anyone
        ("POST", "/api/v1/auth/password-reset/request"),  # pre-authentication
        ("POST", "/api/v1/auth/password-reset/confirm"),  # the token is the credential
        ("POST", "/api/v1/auth/phone/verify/request"),  # deferred stub, always 400
    }
)

# Mutations that need authentication but no permission. Kept separate from
# PUBLIC_ROUTES so that "signed in, no privilege required" is a visibly different
# claim from "open to the world".
AUTHENTICATED_MUTATIONS: frozenset[Route] = frozenset(
    {
        # Acts only on the caller's own sessions. Requiring a permission would
        # mean a user could be denied the ability to sign themselves out.
        ("POST", "/api/v1/auth/logout-all"),
        # A user editing their own name, phone, locale, timezone. None of those
        # fields participates in an authorization decision — the schema forbids
        # `email`, `status`, and `role` outright.
        ("PATCH", "/api/v1/users/me"),
    }
)


# ══════════════════════════════════════════════════════════════════════════
#  route discovery
# ══════════════════════════════════════════════════════════════════════════
def _walk(router: Any, prefix: str = "") -> list[tuple[str, APIRoute]]:
    """Every `APIRoute` in the app, paired with its fully-prefixed path.

    Recursion is required, and the prefix accounting is subtle: a route declared
    directly on a router already carries that router's prefix in `.path`, while a
    route reached through an include carries only the *included* router's prefix.
    So the parent's prefix is added on the way down, never on the way out.
    """
    found: list[tuple[str, APIRoute]] = []
    for route in getattr(router, "routes", []):
        if isinstance(route, APIRoute):
            found.append((prefix + route.path, route))
            continue
        included = getattr(route, "original_router", None)
        if included is not None:
            found.extend(_walk(included, prefix + getattr(router, "prefix", "")))
    return found


def _requirements(route: APIRoute) -> list[PermissionRequirement]:
    """The authorization declarations on a route, read without executing it."""
    flat = get_flat_dependant(route.dependant)
    return [dep.call for dep in flat.dependencies if isinstance(dep.call, PermissionRequirement)]


def _discovered(app: FastAPI) -> dict[Route, APIRoute]:
    routes: dict[Route, APIRoute] = {}
    for path, route in _walk(app.router):
        for method in route.methods - {"HEAD", "OPTIONS"}:
            routes[(method, path)] = route
    return routes


@pytest.fixture(scope="module")
def app() -> FastAPI:
    return create_app()


@pytest.fixture(scope="module")
def routes(app: FastAPI) -> dict[Route, APIRoute]:
    return _discovered(app)


# ══════════════════════════════════════════════════════════════════════════
#  A0 — the walker itself
# ══════════════════════════════════════════════════════════════════════════
def test_the_walker_sees_every_published_route(app: FastAPI, routes: dict[Route, APIRoute]) -> None:
    """Guards against the test passing because it enumerated nothing.

    Every assertion below is a loop over `routes`. If the traversal ever misses a
    branch — a FastAPI upgrade changing how routers nest, a `Mount`, a router
    included some new way — those loops go quiet and the suite stays green while
    the routes they were guarding go unchecked.

    `app.openapi()` builds its path list by FastAPI's own traversal, so agreeing
    with it is independent corroboration rather than a restatement.
    """
    published = set(app.openapi()["paths"])
    walked = {path for (_method, path), route in routes.items() if route.include_in_schema}

    assert walked == published, (
        f"route discovery disagrees with OpenAPI.\n"
        f"  missed by the walk: {sorted(published - walked)}\n"
        f"  not in OpenAPI    : {sorted(walked - published)}"
    )
    assert routes, "no routes discovered at all"


# ══════════════════════════════════════════════════════════════════════════
#  A1 — every route is classified
# ══════════════════════════════════════════════════════════════════════════
def test_every_route_is_public_or_declares_its_authorization(
    routes: dict[Route, APIRoute],
) -> None:
    """The fail-closed one (FR-209).

    A route that is neither allowlisted nor declared fails. Nothing about merely
    existing earns a route an exemption — that is what "deny by default" means.
    """
    undeclared = [
        f"{method} {path}"
        for (method, path), route in sorted(routes.items())
        if (method, path) not in PUBLIC_ROUTES and not _requirements(route)
    ]
    assert not undeclared, (
        "these routes declare no authorization:\n  "
        + "\n  ".join(undeclared)
        + "\n\nAdd require_permission(...) or require_authenticated() to the handler, "
        "or add the route to PUBLIC_ROUTES with a one-line reason (FR-209)."
    )


# ══════════════════════════════════════════════════════════════════════════
#  A2 — declared strings are real
# ══════════════════════════════════════════════════════════════════════════
def test_every_declared_permission_exists_in_the_registry(
    routes: dict[Route, APIRoute],
) -> None:
    """A typo is a permanent, silent 403.

    `user:mange` matches nothing in `role_permissions`, so the route denies
    everyone forever and looks like a permissions-data problem rather than a typo.
    `require_permission` also validates at construction; this asserts it at the
    route level, where the failure message can name the route.
    """
    for (method, path), route in sorted(routes.items()):
        for requirement in _requirements(route):
            unknown = sorted(set(requirement.permissions) - PERMISSION_KEYS)
            assert not unknown, f"{method} {path} requires unregistered permission(s): {unknown}"


# ══════════════════════════════════════════════════════════════════════════
#  A3 — the allowlists cannot rot
# ══════════════════════════════════════════════════════════════════════════
@pytest.mark.parametrize("allowlist_name", ["PUBLIC_ROUTES", "AUTHENTICATED_MUTATIONS"])
def test_the_allowlists_contain_no_routes_that_no_longer_exist(
    routes: dict[Route, APIRoute], allowlist_name: str
) -> None:
    """Otherwise the exemption list becomes an archaeological record.

    A stale entry is not merely untidy. Delete `POST /auth/login`, add a different
    handler at the same path a year later, and it inherits the exemption silently.
    """
    allowlist: frozenset[Route] = {
        "PUBLIC_ROUTES": PUBLIC_ROUTES,
        "AUTHENTICATED_MUTATIONS": AUTHENTICATED_MUTATIONS,
    }[allowlist_name]

    stale = sorted(f"{method} {path}" for method, path in allowlist - set(routes))
    assert not stale, f"{allowlist_name} lists routes that no longer exist:\n  " + "\n  ".join(
        stale
    )


# ══════════════════════════════════════════════════════════════════════════
#  A4 — public and declared are mutually exclusive
# ══════════════════════════════════════════════════════════════════════════
def test_no_public_route_also_declares_a_requirement(routes: dict[Route, APIRoute]) -> None:
    """Catches securing a route and forgetting to un-exempt it.

    The result would be a route that reads as protected in the handler and is
    exempt in the test — so the exemption survives review precisely because the
    code next to it looks correct.
    """
    contradictions = [
        f"{method} {path}"
        for (method, path) in sorted(PUBLIC_ROUTES & set(routes))
        if _requirements(routes[(method, path)])
    ]
    assert not contradictions, (
        "these routes are listed as public but declare a requirement — "
        "remove them from PUBLIC_ROUTES:\n  " + "\n  ".join(contradictions)
    )


# ══════════════════════════════════════════════════════════════════════════
#  A5 — a mutation needs a real permission
# ══════════════════════════════════════════════════════════════════════════
def test_every_mutation_requires_a_permission(routes: dict[Route, APIRoute]) -> None:
    """CLAUDE.md rule 2's actual teeth.

    Without this, `require_authenticated()` becomes a universal escape hatch and
    A1 degrades into "the author typed something". A mutation reachable by any
    signed-in user has to be listed in `AUTHENTICATED_MUTATIONS` by name, which
    is a decision someone makes rather than a default someone inherits.
    """
    exempt = PUBLIC_ROUTES | AUTHENTICATED_MUTATIONS
    offenders = [
        f"{method} {path}"
        for (method, path), route in sorted(routes.items())
        if method in MUTATION_METHODS
        and (method, path) not in exempt
        and not any(not req.is_authenticated_only for req in _requirements(route))
    ]
    assert not offenders, (
        "these mutations are reachable by any authenticated user:\n  "
        + "\n  ".join(offenders)
        + "\n\nEither require a permission, or add the route to AUTHENTICATED_MUTATIONS "
        "with a reason (CLAUDE.md rule 2)."
    )
