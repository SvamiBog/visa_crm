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
    (tmp_path / "secret.txt").write_text("secret")
    monkeypatch.setenv("FRONTEND_DIST", str(dist))
    get_settings.cache_clear()
    yield create_app()
    get_settings.cache_clear()


def raw_get(app, path: str) -> tuple[int, bytes]:
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
    return sent[0]["status"], body


def test_spa_serves_files_and_falls_back_to_index(spa_app) -> None:
    assert raw_get(spa_app, "/robots.txt") == (200, b"robots")
    assert raw_get(spa_app, "/clients/42") == (200, b"<!doctype html>index")


def test_spa_does_not_serve_files_outside_dist(spa_app) -> None:
    status, body = raw_get(spa_app, "/../secret.txt")
    assert status == 200
    assert body == b"<!doctype html>index"
