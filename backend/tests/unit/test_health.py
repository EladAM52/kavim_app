"""Phase 0 acceptance: the app boots and its probes behave correctly."""

from __future__ import annotations

from httpx import AsyncClient


async def test_liveness_is_always_ok(client: AsyncClient) -> None:
    """Liveness must not depend on Postgres or Redis.

    If it did, a brief dependency outage would make the orchestrator kill a
    perfectly healthy container.
    """
    response = await client.get("/health/live")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "alive"
    assert body["version"]


async def test_readiness_reports_each_dependency(client: AsyncClient) -> None:
    """Readiness reports per-dependency state and returns 503 when degraded."""
    response = await client.get("/health/ready")
    assert response.status_code in (200, 503)

    body = response.json()
    assert set(body["checks"]) == {"database", "redis"}

    if response.status_code == 200:
        assert body["status"] == "ready"
        assert all(v == "ok" for v in body["checks"].values())
    else:
        assert body["status"] == "not_ready"
        assert any(v == "unreachable" for v in body["checks"].values())


async def test_api_root_advertises_locales(client: AsyncClient) -> None:
    response = await client.get("/api/v1/")
    assert response.status_code == 200
    body = response.json()
    assert body["api_version"] == "v1"
    assert body["default_locale"] == "he"
    assert "he" in body["locales"]
    assert body["timezone"] == "Asia/Jerusalem"


async def test_request_id_is_echoed(client: AsyncClient) -> None:
    response = await client.get("/health/live", headers={"X-Request-ID": "abc-123"})
    assert response.headers["X-Request-ID"] == "abc-123"


async def test_request_id_is_generated_when_absent(client: AsyncClient) -> None:
    response = await client.get("/health/live")
    assert response.headers.get("X-Request-ID")


async def test_security_headers_present(client: AsyncClient) -> None:
    response = await client.get("/health/live")
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert "Content-Security-Policy" in response.headers


async def test_docs_csp_allows_the_swagger_cdn(client: AsyncClient) -> None:
    """Swagger UI loads its bundle from jsdelivr, so a 'self'-only script-src
    renders an empty page with nothing in the console but a CSP refusal."""
    response = await client.get("/docs")
    assert response.status_code == 200
    csp = response.headers["Content-Security-Policy"]
    assert "script-src 'self' 'unsafe-inline' 'unsafe-eval' https://cdn.jsdelivr.net" in csp
    assert "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net" in csp


async def test_cdn_allowance_does_not_leak_to_other_routes(client: AsyncClient) -> None:
    """The relaxation is scoped to the docs paths. The SPA must never be able to
    load a script from a third-party origin."""
    response = await client.get("/health/live")
    assert "cdn.jsdelivr.net" not in response.headers["Content-Security-Policy"]


async def test_unknown_route_returns_problem_json(client: AsyncClient) -> None:
    """Errors use RFC 7807 so the frontend has one shape to parse (SPEC §9.1)."""
    response = await client.get("/api/v1/does-not-exist")
    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/problem+json")
    body = response.json()
    assert body["status"] == 404
    assert body["instance"] == "/api/v1/does-not-exist"
    assert "code" in body
