"""Password hashes are computed off the event loop (audit 2026-11-27, H4 and H7): argon2
takes 64 MiB and about a quarter of a CPU-second, and while a handler computes it on the
loop the site answers nobody. The handlers go through `make_password_hash` and
`check_password` (app/core/security.py), which run it in the password threads."""

import ast
import threading
from pathlib import Path

from app.core import security

API = Path(__file__).resolve().parents[1] / "app" / "api"
ON_THE_LOOP = {"hash_password", "verify_password"}


def _called(node: ast.Call) -> str | None:
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return None


def test_no_handler_hashes_or_checks_a_password_on_the_event_loop():
    found = [
        f"{path.name}:{node.lineno} {_called(node)}"
        for path in sorted(API.glob("*.py"))
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
        if isinstance(node, ast.Call) and _called(node) in ON_THE_LOOP
    ]
    assert found == []


async def test_a_password_is_hashed_in_the_password_threads(monkeypatch):
    threads = []

    def hash_in(password: str) -> str:
        threads.append(threading.current_thread().name)
        return f"hash of {len(password)}"

    monkeypatch.setattr(security, "hash_password", hash_in)
    assert await security.make_password_hash("temporary-password") == "hash of 18"
    assert len(threads) == 1 and threads[0].startswith("password")
