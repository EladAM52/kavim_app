"""Phase 0 acceptance: the app boots and its probes behave correctly."""

from __future__ import annotations

from unittest import mock

from httpx import ASGITransport, AsyncClient


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


async def test_the_docs_link_a_schema_url_the_browser_can_resolve() -> None:
    """Under a reverse-proxy subpath: prefixed schema link, and **no** root_path.

    `root_path` is the intuitive fix here and it is the opposite of one. It tells
    Starlette the prefix is present in incoming paths — but nginx strips `/kavim`
    before proxying, so every request arrives without it and matches nothing.
    That shipped once and took the deployed app down: the SPA's HTML loaded and
    every asset came back 404, while `/kavim/assets/...` returned 200.

    Prefixing the one URL the docs page *generates* is the whole fix. The schema
    route stays at the root, because that is the path nginx forwards.
    """
    from app.core.config import Settings
    from app.main import create_app

    with_prefix = Settings(  # type: ignore[arg-type]
        APP_PUBLIC_PATH="/kavim", API_DOCS_ENABLED=True, _env_file=None
    )
    with mock.patch("app.main.settings", with_prefix):
        app = create_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
            page = await client.get("/docs")
            schema = await client.get("/openapi.json")

    assert app.root_path == ""
    assert page.status_code == 200
    assert "/kavim/openapi.json" in page.text
    assert schema.status_code == 200


async def test_at_a_host_root_the_schema_link_is_unprefixed() -> None:
    """The default deployment, unchanged: what FastAPI would emit on its own."""
    from app.core.config import Settings
    from app.main import create_app

    with mock.patch("app.main.settings", Settings(API_DOCS_ENABLED=True, _env_file=None)):  # type: ignore[arg-type]
        app = create_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
            page = await client.get("/docs")

    assert '"/openapi.json"' in page.text or "'/openapi.json'" in page.text


async def test_production_docs_still_get_the_cdn_allowance(client: AsyncClient) -> None:
    """The bug this exists to prevent, found on a live server.

    The production branch used to be evaluated first, so `/docs` inherited
    `script-src 'self'` and Swagger's bundle was refused: a password prompt that
    worked, then a completely blank page. The docs allowance has to win over the
    production policy for the three paths it covers.
    """
    from app.core import middleware as middleware_mod

    # Both switches, because that is the only configuration in which the docs
    # exist on a production host: FastAPI mounts them and the proxy asks for a
    # password.
    with (
        mock.patch.object(middleware_mod.settings, "APP_ENV", "production"),
        mock.patch.object(middleware_mod.settings, "API_DOCS_ENABLED", True),
    ):
        response = await client.get("/docs")

    csp = response.headers["Content-Security-Policy"]
    assert "https://cdn.jsdelivr.net" in csp
    # And the production-only header is still applied on the same response.
    assert "Strict-Transport-Security" in response.headers


async def test_production_pages_other_than_docs_stay_strict(client: AsyncClient) -> None:
    from app.core import middleware as middleware_mod

    with (
        mock.patch.object(middleware_mod.settings, "APP_ENV", "production"),
        mock.patch.object(middleware_mod.settings, "API_DOCS_ENABLED", True),
    ):
        response = await client.get("/health/live")

    csp = response.headers["Content-Security-Policy"]
    assert "cdn.jsdelivr.net" not in csp
    assert "script-src 'self';" in csp
