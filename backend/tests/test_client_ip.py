"""Which address the audit log and the login limit take for a client (backlog S17, audit
N1 and N2), with the app served the way start.sh has uvicorn serve it on Railway."""

import logging
import shlex
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient, Response
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine
from starlette.types import ASGIApp
from uvicorn.main import main as uvicorn_command
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from app.api import auth
from app.api.auth import MAX_CLIENT_FAILURES, MAX_FAILED_LOGINS
from app.core import audit
from app.core.security import ClientFailures
from app.models import AuditAction
from tests.helpers import PASSWORD, MakeUser, login, login_first_step
from tests.test_auth import PASSWORD_FORMS

START_SH = Path(__file__).resolve().parents[1] / "start.sh"
ANNA = "anna@example.com"
EDGE = "100.64.0.9"  # Railway's proxy, as the app's connection shows it
REAL = "198.51.100.20"  # the client, as the proxy saw it
OTHER = "198.51.100.21"
FAKE = "203.0.113.7"  # what the client wrote in its own X-Forwarded-For


def served_like_start_sh(app: ASGIApp) -> ASGIApp:
    """The app behind what uvicorn puts in front of it with start.sh's options, read by
    uvicorn's own command line."""
    line = next(ln for ln in START_SH.read_text().splitlines() if ln.startswith("exec uvicorn "))
    args = [arg.replace("${PORT:-8000}", "8000") for arg in shlex.split(line)[2:]]
    options = uvicorn_command.make_context("uvicorn", args).params
    if not options["proxy_headers"]:
        return app
    # uvicorn's default when the option is not given (uvicorn.config.Config).
    trusted = options["forwarded_allow_ips"] or "127.0.0.1,::1"
    return ProxyHeadersMiddleware(app, trusted_hosts=trusted)


@pytest.fixture
async def edge(client: AsyncClient) -> AsyncIterator[AsyncClient]:
    """Requests as Railway's proxy passes them on: from its address, to the app as
    start.sh serves it. The same app as `client`, so the same login limit."""
    app = served_like_start_sh(client._transport.app)
    transport = ASGITransport(app=app, client=(EDGE, 40000))
    async with AsyncClient(transport=transport, base_url=client.base_url) as http:
        yield http


async def sign_in(
    http: AsyncClient,
    forwarded: str | None = None,
    *,
    email: str = ANNA,
    password: str = "wrong-password",
) -> Response:
    headers = {"X-Forwarded-For": forwarded} if forwarded is not None else {}
    body = {"email": email, "password": password}
    return await http.post("/api/auth/login", json=body, headers=headers)


async def logged_ips(owner_engine: AsyncEngine, action: AuditAction) -> list[str | None]:
    async with owner_engine.connect() as conn:
        rows = await conn.execute(
            text("SELECT ip FROM audit_events WHERE action = :action ORDER BY id"),
            {"action": action.value},
        )
    return [row.ip for row in rows]


async def misses(owner_engine: AsyncEngine, email: str = ANNA) -> int:
    async with owner_engine.connect() as conn:
        return await conn.scalar(
            text("SELECT failed_login_count FROM users WHERE email = :email"), {"email": email}
        )


async def test_behind_the_proxy_the_log_takes_the_address_the_proxy_added(
    edge: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine
):
    # The client may send an X-Forwarded-For of its own: the last entry is the one
    # Railway's proxy wrote (audit N1).
    await make_user(ANNA, two_factor=False)
    assert (await sign_in(edge, f"{FAKE}, {REAL}")).status_code == 401
    assert (await sign_in(edge, f"{FAKE}, {REAL}", password=PASSWORD)).status_code == 200

    assert await logged_ips(owner_engine, AuditAction.LOGIN_FAILED) == [REAL]
    assert await logged_ips(owner_engine, AuditAction.LOGIN) == [REAL]


async def test_the_login_limit_counts_the_address_the_proxy_added(
    edge: AsyncClient, make_user: MakeUser
):
    # A new first entry on every try does not make a new client.
    await make_user(ANNA, two_factor=False)
    for n in range(MAX_CLIENT_FAILURES):
        guess = await sign_in(edge, f"203.0.113.{n}, {REAL}", email=f"guess-{n}@example.com")
        assert guess.status_code == 401
    paused = await sign_in(edge, f"203.0.113.250, {REAL}", password=PASSWORD)
    assert paused.status_code == 429

    # Someone else behind the same proxy is not paused.
    assert (await sign_in(edge, f"203.0.113.250, {OTHER}", password=PASSWORD)).status_code == 200


async def test_a_single_forwarded_address_is_the_client(
    edge: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine
):
    await make_user(ANNA, two_factor=False)
    assert (await sign_in(edge, REAL)).status_code == 401
    assert await logged_ips(owner_engine, AuditAction.LOGIN_FAILED) == [REAL]


async def test_without_a_proxy_the_connection_is_the_client(
    client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine
):
    # A developer's machine: uvicorn without start.sh, no header (httpx's client is
    # 127.0.0.1).
    await make_user(ANNA, two_factor=False)
    assert (await sign_in(client)).status_code == 401
    assert await logged_ips(owner_engine, AuditAction.LOGIN_FAILED) == ["127.0.0.1"]


@pytest.mark.parametrize(
    "forwarded",
    ["a" * 46, "not-an-address", f"{REAL}, nonsense"],
    ids=["too long", "not an address", "last entry not an address"],
)
async def test_a_forwarded_value_that_is_not_an_address_is_not_recorded(
    edge: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine, forwarded: str
):
    # Checked before it is written: the login neither fails on it nor loses its miss,
    # and the log keeps no made-up address (audit N2).
    await make_user(ANNA, two_factor=False)
    assert (await sign_in(edge, forwarded)).status_code == 401
    assert await logged_ips(owner_engine, AuditAction.LOGIN_FAILED) == [None]
    assert await misses(owner_engine) == 1


def log_refuses_entries(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every audit entry the login code writes is one the database refuses: its address
    is longer than the column."""

    def record(*args, **kwargs) -> None:
        token = audit.client_ip.set("x" * 46)
        try:
            audit.record(*args, **kwargs)
        finally:
            audit.client_ip.reset(token)

    monkeypatch.setattr(auth, "record", record)


async def test_a_wrong_password_counts_when_the_log_refuses_its_entry(
    client: AsyncClient,
    make_user: MakeUser,
    owner_engine: AsyncEngine,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
):
    await make_user(ANNA, two_factor=False)
    client._transport.app.state.client_failures = ClientFailures(1, 900)
    log_refuses_entries(monkeypatch)
    # The migrations of the test database run in this process, and Alembic's logging
    # config switches off the loggers made before it.
    monkeypatch.setattr(logging.getLogger("app.api.auth"), "disabled", False)

    with caplog.at_level(logging.ERROR, logger="app.api.auth"):
        wrong = await sign_in(client)
    assert wrong.status_code == 401 and wrong.json() == {"detail": "invalid_credentials"}
    assert await logged_ips(owner_engine, AuditAction.LOGIN_FAILED) == []
    assert await misses(owner_engine) == 1
    # Not silent, and with ids only: nothing of what the database turned down.
    assert "The audit log refused a failed login of user" in caplog.text
    assert "x" * 46 not in caplog.text and ANNA not in caplog.text
    # The client's limit kept the miss too.
    assert (await sign_in(client, password=PASSWORD)).status_code == 429


async def test_a_wrong_code_counts_when_the_log_refuses_its_entry(
    client: AsyncClient,
    make_user: MakeUser,
    owner_engine: AsyncEngine,
    monkeypatch: pytest.MonkeyPatch,
):
    await make_user(ANNA)
    assert (await login_first_step(client, ANNA)).json() == {"mfa_required": True}
    log_refuses_entries(monkeypatch)

    wrong = await client.post("/api/auth/login/verify", json={"code": "000000"})
    assert wrong.status_code == 401 and wrong.json() == {"detail": "invalid_code"}
    assert await logged_ips(owner_engine, AuditAction.LOGIN_FAILED) == []
    assert await misses(owner_engine) == 1


@pytest.mark.parametrize("form", PASSWORD_FORMS)
async def test_guessing_in_settings_locks_even_when_the_log_refuses_entries(
    client: AsyncClient,
    make_user: MakeUser,
    owner_engine: AsyncEngine,
    monkeypatch: pytest.MonkeyPatch,
    form: str,
):
    # A stolen session gets no more tries because the log turned an entry down (N2).
    url, body, two_factor = PASSWORD_FORMS[form]
    await make_user(ANNA, two_factor=two_factor)
    await login(client, ANNA)
    log_refuses_entries(monkeypatch)

    tries = [
        await client.post(url, json=body(f"guess-{n:04d}-password"))
        for n in range(MAX_FAILED_LOGINS)
    ]
    assert [r.status_code for r in tries] == [400] * (MAX_FAILED_LOGINS - 1) + [423]
    assert [r.json()["detail"] for r in tries] == ["invalid_current_password"] * (
        MAX_FAILED_LOGINS - 1
    ) + ["account_locked"]
    assert await logged_ips(owner_engine, AuditAction.LOGIN_FAILED) == []
    assert (await client.get("/api/auth/me")).status_code == 401
