"""The session cookie a neighbouring site cannot plant (backlog S18, audit N10).

On HTTPS the cookie is named __Host-…: a browser takes a cookie of that name only from the
site's own host, Secure, for Path=/ and without Domain, so another site under the same
parent domain cannot set one. A developer's machine over http keeps the plain name, which
a browser would refuse with the prefix. Sessions signed in under the plain name before the
change keep working for a while and move to the new name.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient, Response
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.api import deps
from app.api.deps import HOST_SESSION_COOKIE, SESSION_COOKIE
from app.core.config import get_settings
from app.core.request_limit import MAX_FORM_BYTES, MAX_UPLOAD_BYTES
from tests.support.helpers import CLOCK, MakeUser, login, login_first_step
from tests.test_request_limit import JSON, MIB, NO_CONTACT, pdf, upload

ME = "/api/auth/me"
LOCKED_TO_THE_HOST = {"path=/", "secure", "httponly", "samesite=lax"}

Site = Callable[[str], None]


@pytest.fixture
def site(monkeypatch: pytest.MonkeyPatch) -> Site:
    """The environment the session cookie sees: a stand or the real site is on HTTPS, a
    developer's machine ("local", "test") on plain http. The rest of the app stays as the
    tests run it."""

    def on(env: str) -> None:
        settings = get_settings().model_copy(update={"env": env})
        monkeypatch.setattr(deps, "get_settings", lambda: settings)

    return on


@pytest.fixture
def during_the_transition(monkeypatch: pytest.MonkeyPatch) -> None:
    """The plain name is still taken on HTTPS, whatever day the tests run on."""
    until = datetime.now(UTC) + timedelta(days=1)
    monkeypatch.setattr(deps, "PLAIN_SESSION_COOKIE_UNTIL", until)


def https(client: AsyncClient) -> AsyncClient:
    """A browser on the same app over HTTPS: it keeps and sends Secure cookies."""
    return AsyncClient(transport=client._transport, base_url="https://testserver")


@dataclass(frozen=True)
class SetCookie:
    value: str
    attributes: set[str]  # lower case: "path=/", "secure", "max-age=0", …

    @property
    def max_age(self) -> int:
        return next(int(a.split("=")[1]) for a in self.attributes if a.startswith("max-age="))

    @property
    def domain(self) -> str | None:
        return next((a.split("=")[1] for a in self.attributes if a.startswith("domain=")), None)


def set_cookies(response: Response) -> dict[str, SetCookie]:
    """The cookies a response sets or deletes, by name."""
    cookies = {}
    for line in response.headers.get_list("set-cookie"):
        name, _, rest = line.partition("=")
        value, *attributes = (part.strip() for part in rest.split(";"))
        cookies[name] = SetCookie(value, {a.lower() for a in attributes})
    return cookies


async def session_token(client: AsyncClient, email: str) -> str:
    """A fresh session of the user, signed in over HTTPS, whatever name its cookie has."""
    async with https(client) as browser:
        assert (await login(browser, email)).status_code == 200
        return browser.cookies.get(HOST_SESSION_COOKIE) or browser.cookies[SESSION_COOKIE]


@pytest.mark.parametrize("env", ["staging", "production"])
async def test_on_https_the_session_cookie_is_locked_to_the_host(
    client: AsyncClient, make_user: MakeUser, site: Site, env: str
):
    site(env)
    await make_user("anna@example.com")  # with two-factor login: two steps, two cookies
    async with https(client) as browser:
        first_step = await login_first_step(browser, "anna@example.com")
        assert first_step.json() == {"mfa_required": True}
        code = CLOCK.next_code()
        verified = await browser.post("/api/auth/login/verify", json={"code": code})
        assert verified.status_code == 200, verified.text

        for response in (first_step, verified):
            cookies = set_cookies(response)
            assert HOST_SESSION_COOKIE in cookies, cookies
            cookie = cookies[HOST_SESSION_COOKIE]
            assert cookie.value and cookie.attributes >= LOCKED_TO_THE_HOST
            assert cookie.domain is None
            # A plain cookie left from before the change is cleared, never set.
            assert SESSION_COOKIE not in cookies or cookies[SESSION_COOKIE].max_age == 0

        assert set(browser.cookies.keys()) == {HOST_SESSION_COOKIE}
        assert (await browser.get(ME)).json()["email"] == "anna@example.com"


@pytest.mark.parametrize("env", ["local", "test"])
async def test_over_http_on_a_developers_machine_the_plain_name_stays(
    client: AsyncClient, make_user: MakeUser, site: Site, env: str
):
    """The prefix needs Secure, and Secure needs HTTPS: a browser would drop the cookie."""
    site(env)
    await make_user("anna@example.com")
    response = await login(client, "anna@example.com")
    cookies = set_cookies(response)
    assert set(cookies) == {SESSION_COOKIE}
    assert "secure" not in cookies[SESSION_COOKIE].attributes
    assert {"path=/", "httponly", "samesite=lax"} <= cookies[SESSION_COOKIE].attributes
    assert (await client.get(ME)).json()["email"] == "anna@example.com"


@pytest.mark.parametrize("path", [ME, "/api/workspace"])
async def test_a_session_under_the_plain_name_moves_to_the_host_name(
    client: AsyncClient,
    make_user: MakeUser,
    owner_engine: AsyncEngine,
    site: Site,
    path: str,
    during_the_transition: None,
):
    """A browser signed in before the change still holds the plain name: its next request
    is served and the cookie is renamed, for what is left of the session."""
    site("production")
    await make_user("anna@example.com")
    token = await session_token(client, "anna@example.com")
    async with owner_engine.begin() as conn:
        await conn.execute(text("UPDATE user_sessions SET expires_at = now() + interval '1 hour'"))

    async with https(client) as browser:
        response = await browser.get(path, headers={"Cookie": f"{SESSION_COOKIE}={token}"})
        assert response.status_code == 200, response.text
        cookies = set_cookies(response)
        assert HOST_SESSION_COOKIE in cookies, cookies
        moved = cookies[HOST_SESSION_COOKIE]
        assert moved.value == token
        assert moved.attributes >= LOCKED_TO_THE_HOST and moved.domain is None
        assert 3500 < moved.max_age <= 3600
        assert cookies[SESSION_COOKIE].max_age == 0

        # The browser now holds the new name only, and it is enough.
        assert set(browser.cookies.keys()) == {HOST_SESSION_COOKIE}
        again = await browser.get(path)
        assert again.status_code == 200 and "set-cookie" not in again.headers


async def test_after_the_transition_the_plain_name_signs_nobody_in(
    client: AsyncClient, make_user: MakeUser, site: Site, monkeypatch: pytest.MonkeyPatch
):
    site("production")
    await make_user("anna@example.com")
    token = await session_token(client, "anna@example.com")
    monkeypatch.setattr(
        deps, "PLAIN_SESSION_COOKIE_UNTIL", datetime.now(UTC) - timedelta(seconds=1)
    )
    async with https(client) as browser:
        plain = await browser.get(ME, headers={"Cookie": f"{SESSION_COOKIE}={token}"})
        assert (plain.status_code, plain.json()) == (401, {"detail": "not_authenticated"})
        host = await browser.get(ME, headers={"Cookie": f"{HOST_SESSION_COOKIE}={token}"})
        assert host.json()["email"] == "anna@example.com"


async def test_on_a_developers_machine_the_plain_name_has_no_end(
    client: AsyncClient, make_user: MakeUser, site: Site, monkeypatch: pytest.MonkeyPatch
):
    site("local")
    await make_user("anna@example.com")
    await login(client, "anna@example.com")
    monkeypatch.setattr(
        deps, "PLAIN_SESSION_COOKIE_UNTIL", datetime.now(UTC) - timedelta(seconds=1)
    )
    response = await client.get(ME)
    assert response.json()["email"] == "anna@example.com"
    assert "set-cookie" not in response.headers


@pytest.mark.parametrize("planted_first", [False, True])
async def test_with_both_names_only_the_host_one_counts(
    client: AsyncClient,
    make_user: MakeUser,
    site: Site,
    planted_first: bool,
    during_the_transition: None,
):
    """A neighbouring site can plant the plain name (Domain=<parent>) but not the __Host-
    one: the planted session must not take the place of the user's own."""
    site("production")
    await make_user("anna@example.com")
    await make_user("olga@example.com")
    anna = await session_token(client, "anna@example.com")
    planted = await session_token(client, "olga@example.com")

    pairs = [f"{HOST_SESSION_COOKIE}={anna}", f"{SESSION_COOKIE}={planted}"]
    if planted_first:
        pairs.reverse()
    async with https(client) as browser:
        response = await browser.get(ME, headers={"Cookie": "; ".join(pairs)})
    assert response.json()["email"] == "anna@example.com"
    assert HOST_SESSION_COOKIE not in set_cookies(response)  # nothing renamed to the plant


@pytest.mark.parametrize(
    ("env", "name"),
    [
        ("production", HOST_SESSION_COOKIE),
        ("production", SESSION_COOKIE),
        ("local", SESSION_COOKIE),
    ],
)
async def test_a_session_name_sent_twice_signs_nobody_in(
    client: AsyncClient,
    make_user: MakeUser,
    site: Site,
    env: str,
    name: str,
    during_the_transition: None,
):
    """Which of two copies is the user's own cannot be told: neither is trusted (audit N10,
    the server keeps no longer the last one it is sent)."""
    site(env)
    await make_user("anna@example.com")
    await make_user("olga@example.com")
    anna = await session_token(client, "anna@example.com")
    planted = await session_token(client, "olga@example.com")

    async with https(client) as browser:
        for cookie in (
            f"{name}={anna}; {name}={planted}",
            f"{name}={planted}; {name}={anna}",
            f"{name}={anna}; {name}={anna}",
        ):
            response = await browser.get(ME, headers={"Cookie": cookie})
            assert (response.status_code, response.json()) == (
                401,
                {"detail": "not_authenticated"},
            ), cookie
        # The same, split over two Cookie header lines.
        lines = [("Cookie", f"{name}={anna}"), ("Cookie", f"{name}={planted}")]
        response = await browser.get(ME, headers=lines)
        assert response.status_code == 401
        if name == HOST_SESSION_COOKIE:
            # A doubled __Host- cookie does not fall back to the plain name either.
            cookie = f"{name}={anna}; {name}={planted}; {SESSION_COOKIE}={anna}"
            response = await browser.get(ME, headers={"Cookie": cookie})
            assert response.status_code == 401


async def test_a_login_waiting_for_its_code_under_the_plain_name_finishes_under_the_host_name(
    client: AsyncClient, make_user: MakeUser, site: Site, during_the_transition: None
):
    """The password was typed before the deploy, the code after it."""
    site("production")
    await make_user("anna@example.com")
    async with https(client) as browser:
        await login_first_step(browser, "anna@example.com")
        token = browser.cookies.get(HOST_SESSION_COOKIE) or browser.cookies[SESSION_COOKIE]
        browser.cookies.clear()
        verified = await browser.post(
            "/api/auth/login/verify",
            json={"code": CLOCK.next_code()},
            headers={"Cookie": f"{SESSION_COOKIE}={token}"},
        )
        assert verified.status_code == 200, verified.text
        cookies = set_cookies(verified)
        assert HOST_SESSION_COOKIE in cookies, cookies
        assert cookies[HOST_SESSION_COOKIE].value == token
        assert cookies[SESSION_COOKIE].max_age == 0
        assert (await browser.get(ME)).json()["email"] == "anna@example.com"


async def test_logging_out_deletes_both_names(client: AsyncClient, make_user: MakeUser, site: Site):
    site("production")
    await make_user("anna@example.com")
    async with https(client) as browser:
        await login(browser, "anna@example.com")
        token = browser.cookies.get(HOST_SESSION_COOKIE) or browser.cookies[SESSION_COOKIE]
        logout = await browser.post("/api/auth/logout")
        assert logout.status_code == 204
        cookies = set_cookies(logout)
        assert set(cookies) == {HOST_SESSION_COOKIE, SESSION_COOKIE}, cookies
        for cookie in cookies.values():
            assert cookie.max_age == 0 and "path=/" in cookie.attributes
        # A browser takes a __Host- cookie, the one that deletes it too, only when Secure.
        assert "secure" in cookies[HOST_SESSION_COOKIE].attributes
        assert not browser.cookies

        # The session itself is over, under either name.
        for name in (HOST_SESSION_COOKIE, SESSION_COOKIE):
            response = await browser.get(ME, headers={"Cookie": f"{name}={token}"})
            assert response.status_code == 401


async def test_logging_out_under_the_plain_name_ends_the_session(
    client: AsyncClient, make_user: MakeUser, site: Site, during_the_transition: None
):
    site("production")
    await make_user("anna@example.com")
    token = await session_token(client, "anna@example.com")
    async with https(client) as browser:
        plain = {"Cookie": f"{SESSION_COOKIE}={token}"}
        logout = await browser.post("/api/auth/logout", headers=plain)
        assert set(set_cookies(logout)) == {HOST_SESSION_COOKIE, SESSION_COOKIE}
        response = await browser.get(ME, headers={"Cookie": f"{HOST_SESSION_COOKIE}={token}"})
        assert response.status_code == 401


async def test_the_upload_limit_finds_the_session_under_the_host_name(
    client: AsyncClient, make_user: MakeUser, site: Site
):
    """The request limit looks the session up before the body is read (app/main.py,
    backlog S1): a signed-in upload must get past it to the route."""
    site("production")
    await make_user("anna@example.com")
    async with https(client) as browser:
        await login(browser, "anna@example.com")
        assert set(browser.cookies.keys()) == {HOST_SESSION_COOKIE}
        body = upload(pdf(15 * MIB * 3 // 4))
        assert MAX_FORM_BYTES < len(body) < MAX_UPLOAD_BYTES
        url = f"/api/contacts/{NO_CONTACT}/documents"
        response = await browser.post(url, content=body, headers=JSON)
    assert response.status_code == 404, response.text
