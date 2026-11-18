"""The API docs and schema are for developers: every environment has them but production."""

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


def test_production_has_no_api_docs_or_schema(app_in) -> None:
    # The schema maps every endpoint and error code: nobody outside needs that map of the
    # real site. The page fallback does not stand in for them either: a clean 404.
    app = app_in("production")
    for path in ("/api/docs", "/api/openapi.json"):
        status, _, body = raw_response(app, path)
        assert (status, body) == (404, b'{"detail":"Not Found"}'), path
    # Nor is the schema reachable through ReDoc: that path is just a page of the app.
    assert raw_response(app, "/redoc")[::2] == (200, INDEX)


@pytest.mark.parametrize("env", ["local", "staging"])
def test_development_and_stands_keep_api_docs_and_schema(app_in, env: str) -> None:
    # Developers and the stands' security scan use them (CLAUDE.md, docs/staging.md).
    app = app_in(env)
    status, _, body = raw_response(app, "/api/docs")
    assert status == 200
    assert b"swagger-ui" in body
    status, _, body = raw_response(app, "/api/openapi.json")
    assert status == 200
    assert "/api/health" in json.loads(body)["paths"]
    # Swagger UI is the one docs page: ReDoc never worked under the site's CSP.
    assert raw_response(app, "/redoc")[::2] == (200, INDEX)
