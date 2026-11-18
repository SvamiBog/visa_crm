import asyncio
from pathlib import Path

import pytest

from app.core.config import get_settings
from app.main import create_app


@pytest.fixture
def spa_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html>index")
    (dist / "robots.txt").write_text("robots")
    (dist / "assets" / "index-D1NqODF2.js").write_text("main")
    (dist / "assets" / "ocr" / "eng-4.0.0_best_int").mkdir(parents=True)
    (dist / "assets" / "ocr" / "eng-4.0.0_best_int" / "eng.traineddata.gz").write_text("model")
    (tmp_path / "secret.txt").write_text("secret")
    monkeypatch.setenv("FRONTEND_DIST", str(dist))
    get_settings.cache_clear()
    yield create_app()
    get_settings.cache_clear()


def raw_get(app, path: str) -> tuple[int, bytes]:
    status, _, body = raw_response(app, path)
    return status, body


def raw_response(app, path: str) -> tuple[int, dict[str, str], bytes]:
    """Send a request with the path as-is: HTTP clients normalize "../" away, servers do not."""
    sent: list[dict] = []
    received = False

    async def receive() -> dict:
        nonlocal received
        if not received:
            received = True
            return {"type": "http.request", "body": b""}
        await asyncio.sleep(1)
        return {"type": "http.disconnect"}

    async def send(message: dict) -> None:
        sent.append(message)

    scope = {
        "type": "http",
        "method": "GET",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "headers": [],
        "http_version": "1.1",
        "scheme": "http",
        "server": ("test", 80),
        "client": ("test", 1),
        "root_path": "",
    }
    asyncio.run(app(scope, receive, send))
    body = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    headers = {k.decode().lower(): v.decode() for k, v in sent[0]["headers"]}
    return sent[0]["status"], headers, body


def test_spa_serves_files_and_falls_back_to_index(spa_app) -> None:
    assert raw_get(spa_app, "/robots.txt") == (200, b"robots")
    assert raw_get(spa_app, "/clients/42") == (200, b"<!doctype html>index")


def test_spa_does_not_serve_files_outside_dist(spa_app) -> None:
    status, body = raw_get(spa_app, "/../secret.txt")
    assert status == 200
    assert body == b"<!doctype html>index"


def test_built_files_are_cached_and_the_page_is_always_checked(spa_app) -> None:
    # A hashed file never changes: the browser keeps it without asking again.
    status, headers, _ = raw_response(spa_app, "/assets/index-D1NqODF2.js")
    assert status == 200
    assert headers["cache-control"] == "public, max-age=31536000, immutable"
    # The page names the current files, so after a deploy the browser must fetch it anew.
    for path in ("/", "/app/analytics"):
        status, headers, body = raw_response(spa_app, path)
        assert (status, body) == (200, b"<!doctype html>index")
        assert headers["cache-control"] == "no-cache"
    # Files copied as they are (the passport reader's model) keep the default caching.
    status, headers, _ = raw_response(spa_app, "/assets/ocr/eng-4.0.0_best_int/eng.traineddata.gz")
    assert status == 200
    assert "cache-control" not in headers


def test_a_file_of_an_old_build_is_not_found(spa_app) -> None:
    # An open tab from before a deploy asks for a section's old file: a clean 404, not the
    # page, so the app sees the import fail and reloads (frontend/src/components/LazyPage.tsx).
    status, headers, _ = raw_response(spa_app, "/assets/AnalyticsPage-OldHash1.js")
    assert status == 404
    assert "cache-control" not in headers
