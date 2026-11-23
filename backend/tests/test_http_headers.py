"""Security headers on every response (app/core/http_headers.py)."""

import pytest
from httpx import AsyncClient

from app.core.config import get_settings


async def test_responses_carry_security_headers(client: AsyncClient):
    response = await client.get("/api/health")
    headers = response.headers
    assert headers["x-content-type-options"] == "nosniff"
    assert headers["x-frame-options"] == "DENY"
    assert "frame-ancestors 'none'" in headers["content-security-policy"]
    assert "script-src 'self' 'wasm-unsafe-eval'" in headers["content-security-policy"]
    assert "unsafe-inline" not in headers["content-security-policy"]
    assert headers["cache-control"] == "no-store"
    # Tests and local development: no HTTPS, and not for search engines.
    assert "strict-transport-security" not in headers
    assert headers["x-robots-tag"] == "noindex, nofollow"
    # The API docs keep their CDN scripts.
    assert "content-security-policy" not in (await client.get("/api/docs")).headers


async def test_styles_and_fonts_come_only_from_the_site_itself(client: AsyncClient):
    # The fonts are served from our own domain (frontend/src/fonts.css): the page asks
    # nothing of Google, so a specialist's IP address does not reach it.
    policy = (await client.get("/api/health")).headers["content-security-policy"]
    directives = dict(part.strip().split(" ", 1) for part in policy.split(";"))
    assert directives["style-src"] == "'self'"
    assert directives["font-src"] == "'self'"
    assert "google" not in policy


async def test_production_is_indexed_and_served_over_https_only(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
):
    try:
        with monkeypatch.context() as production:
            production.setenv("ENV", "production")
            get_settings.cache_clear()
            headers = (await client.get("/api/health")).headers
    finally:
        get_settings.cache_clear()  # back to the tests' own ENV
    assert "x-robots-tag" not in headers
    assert headers["strict-transport-security"].startswith("max-age=31536000")
