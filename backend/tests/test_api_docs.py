"""The API docs and schema are for developers: only a developer's machine has them.

The stands are reachable from the internet like the real site, and Swagger UI loads its
scripts from a CDN without integrity checks (audit h15.2, backlog S19)."""

import json
from pathlib import Path

import pytest

from app.core.config import get_settings
from app.main import create_app
from tests.test_spa import raw_response

INDEX = b"<!doctype html>index"


@pytest.fixture
def app_in(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """The app with a built frontend, as it starts in the given environment."""
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_bytes(INDEX)
    monkeypatch.setenv("FRONTEND_DIST", str(dist))

    def start(env: str):
        monkeypatch.setenv("ENV", env)
        get_settings.cache_clear()
        return create_app()

    yield start
    get_settings.cache_clear()


@pytest.mark.parametrize("env", ["production", "staging"])
def test_the_site_and_the_stands_have_no_api_docs_or_schema(app_in, env: str) -> None:
    # The schema maps every endpoint and error code: nobody outside needs that map of the
    # site. The page fallback does not stand in for them either: a clean 404.
    app = app_in(env)
    for path in ("/api/docs", "/api/openapi.json"):
        status, _, body = raw_response(app, path)
        assert (status, body) == (404, b'{"detail":"Not Found"}'), path
    # Nor is the schema reachable through ReDoc: that path is just a page of the app.
    assert raw_response(app, "/redoc")[::2] == (200, INDEX)


@pytest.mark.parametrize("env", ["local", "test"])
def test_a_developers_machine_keeps_api_docs_and_schema(app_in, env: str) -> None:
    # Developers use them (CLAUDE.md: /api/docs on the local API).
    app = app_in(env)
    status, _, body = raw_response(app, "/api/docs")
    assert status == 200
    assert b"swagger-ui" in body
    status, _, body = raw_response(app, "/api/openapi.json")
    assert status == 200
    assert "/api/health" in json.loads(body)["paths"]
    # Swagger UI is the one docs page: ReDoc never worked under the site's CSP.
    assert raw_response(app, "/redoc")[::2] == (200, INDEX)
