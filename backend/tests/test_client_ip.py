"""Which address the audit log and the login limit take for a client (backlog S17 and S22,
audit N1 and N2), with the app served the way start.sh has uvicorn serve it on Railway."""

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
from tests.support.helpers import PASSWORD, MakeUser, login, login_first_step
from tests.test_auth import PASSWORD_FORMS

START_SH = Path(__file__).resolve().parents[1] / "start.sh"
ANNA = "anna@example.com"
EDGE = "100.64.0.9"  # Railway's proxy, as the app's connection shows it
NODE = "192.0.2.33"  # a Railway node, which adds itself last to X-Forwarded-For
REAL = "198.51.100.20"  # the client, as Railway's edge saw it
OTHER = "198.51.100.21"
FAKE = "203.0.113.7"  # what a client writes in headers of its own


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


def through_railway(client: str, forwarded: str | None = None) -> dict[str, str]:
    """The headers a request of `client` reaches the app with on Railway (staging,
    24.11.2026): the client first in X-Forwarded-For and a node of Railway's last, and the
    client in X-Real-IP."""
    return {"X-Real-IP": client, "X-Forwarded-For": forwarded or f"{client}, {NODE}"}


async def sign_in(
    http: AsyncClient,
    headers: dict[str, str] | None = None,
    *,
    email: str = ANNA,
    password: str = "wrong-password",
) -> Response:
    body = {"email": email, "password": password}
    return await http.post("/api/auth/login", json=body, headers=headers or {})


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


async def test_behind_railway_the_log_takes_the_client_not_the_node(
    edge: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine
):
    # The last entry of X-Forwarded-For is Railway's own node (audit_events on staging got
    # it with the code of S17), and what the client sends itself proves nothing (N1).
    await make_user(ANNA, two_factor=False)
    assert (await sign_in(edge, through_railway(REAL))).status_code == 401
    sent = through_railway(REAL, f"{FAKE}, {REAL}, {NODE}")
    assert (await sign_in(edge, sent, password=PASSWORD)).status_code == 200

    assert await logged_ips(owner_engine, AuditAction.LOGIN_FAILED) == [REAL]
    assert await logged_ips(owner_engine, AuditAction.LOGIN) == [REAL]


async def test_a_client_guessing_does_not_pause_others_behind_the_same_node(
    edge: AsyncClient, make_user: MakeUser
):
    # Every request comes from the same node of Railway's: the limit counts the client.
    await make_user(ANNA, two_factor=False)
    for n in range(MAX_CLIENT_FAILURES):
        sent = through_railway(REAL, f"203.0.113.{n}, {REAL}, {NODE}")
        assert (await sign_in(edge, sent, email=f"guess-{n}@example.com")).status_code == 401
    assert (await sign_in(edge, through_railway(REAL), password=PASSWORD)).status_code == 429

    assert (await sign_in(edge, through_railway(OTHER), password=PASSWORD)).status_code == 200


async def test_without_a_proxy_the_connection_is_the_client(
    client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine
):
    # A developer's machine: uvicorn without start.sh, no header (httpx's client is
    # 127.0.0.1).
    await make_user(ANNA, two_factor=False)
    assert (await sign_in(client)).status_code == 401
    assert await logged_ips(owner_engine, AuditAction.LOGIN_FAILED) == ["127.0.0.1"]


@pytest.mark.parametrize(
    "real_ip",
    ["a" * 46, "not-an-address", f"{REAL}, {FAKE}"],
    ids=["too long", "not an address", "two addresses"],
)
async def test_an_x_real_ip_that_is_not_an_address_is_not_recorded(
    edge: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine, real_ip: str
):
    # Checked before it is written: the login neither fails on it nor loses its miss, the
    # log keeps no made-up address and takes no entry of X-Forwarded-For instead (N2).
    await make_user(ANNA, two_factor=False)
    sent = {"X-Real-IP": real_ip, "X-Forwarded-For": f"{REAL}, {NODE}"}
    assert (await sign_in(edge, sent)).status_code == 401
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
