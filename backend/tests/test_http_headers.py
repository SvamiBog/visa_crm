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
    assert "script-src 'self'" in headers["content-security-policy"]
    assert headers["cache-control"] == "no-store"
    # Tests and local development: no HTTPS, and not for search engines.
    assert "strict-transport-security" not in headers
    assert headers["x-robots-tag"] == "noindex, nofollow"
    # The API docs keep their CDN scripts.
    assert "content-security-policy" not in (await client.get("/api/docs")).headers


async def test_production_is_indexed_and_served_over_https_only(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("ENV", "production")
    get_settings.cache_clear()
    try:
        headers = (await client.get("/api/health")).headers
    finally:
        monkeypatch.delenv("ENV")
        get_settings.cache_clear()
    assert "x-robots-tag" not in headers
    assert headers["strict-transport-security"].startswith("max-age=31536000")
