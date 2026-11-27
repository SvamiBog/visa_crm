"""A changing request from another site is refused (backlog S19, audit h01.3).

The session cookie is SameSite=Lax; the server also checks where a POST, PUT, PATCH or
DELETE comes from (app/core/http_headers.py), so a page on another site, or on a
neighbouring subdomain of the same hosting, cannot act in a signed-in specialist's name.
"""

import logging

import pytest
from httpx import AsyncClient

from tests.support.helpers import PASSWORD, MakeUser

# The test client's own address (conftest.py: base_url="http://testserver").
OWN = "http://testserver"
LOGIN = "/api/auth/login"


async def log_in(client: AsyncClient, headers: dict[str, str]):
    return await client.post(
        LOGIN, json={"email": "anna@example.com", "password": PASSWORD}, headers=headers
    )


@pytest.mark.parametrize(
    "headers",
    [
        {"Origin": "https://evil.example"},
        {"Origin": "null"},  # a sandboxed frame or a redirect from elsewhere
        {"Origin": "http://testserver.evil.example"},
        {"Sec-Fetch-Site": "cross-site"},
        # A neighbouring subdomain of the same hosting is the same site, not the same origin.
        {"Sec-Fetch-Site": "same-site", "Origin": "https://other.up.railway.app"},
        {"Sec-Fetch-Site": "cross-site", "Origin": OWN},
    ],
)
async def test_a_changing_request_from_another_site_is_refused(
    client: AsyncClient, make_user: MakeUser, headers: dict[str, str]
):
    await make_user("anna@example.com")
    refused = await log_in(client, headers)
    assert refused.status_code == 403
    assert refused.json() == {"detail": "forbidden"}
    assert "set-cookie" not in refused.headers
    # The refusal carries the site's security headers like any answer.
    assert refused.headers["x-frame-options"] == "DENY"


@pytest.mark.parametrize("method", ["PUT", "PATCH", "DELETE"])
async def test_every_changing_method_is_checked(client: AsyncClient, method: str):
    response = await client.request(
        method, "/api/auth/preferences", headers={"Origin": "https://evil.example"}
    )
    assert response.status_code == 403
    assert response.json() == {"detail": "forbidden"}


async def test_a_refusal_is_logged_without_what_the_sender_wrote(
    client: AsyncClient, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
):
    # The migrations of the test database run in this process, and Alembic's logging config
    # switches off the loggers made before them.
    monkeypatch.setattr(logging.getLogger("app.core.http_headers"), "disabled", False)
    caplog.set_level(logging.WARNING, logger="app.core.http_headers")
    await client.post("/api/contacts/1%0Aforged-line", headers={"Origin": "https://evil.example"})
    await client.delete("/api/contacts/2", headers={"Sec-Fetch-Site": "cross-site"})
    # No path (no ids in the log, nor a line the sender made up), just which header decided.
    assert caplog.messages == [
        "Refused a POST from another site, by Origin",
        "Refused a DELETE from another site, by Sec-Fetch-Site",
    ]


@pytest.mark.parametrize(
    "headers",
    [
        {"Origin": OWN, "Sec-Fetch-Site": "same-origin"},  # the site's own page
        {"Origin": OWN},  # a browser that does not send Sec-Fetch-Site
        {"Origin": "http://TestServer"},  # a host name in any case
        {"Sec-Fetch-Site": "none"},  # typed or bookmarked by the user, not sent by a site
        {},  # not a browser: a script, the CLI, the test client
    ],
)
async def test_the_sites_own_requests_go_through(
    client: AsyncClient, make_user: MakeUser, headers: dict[str, str]
):
    await make_user("anna@example.com")
    response = await log_in(client, headers)
    assert response.status_code == 200, response.text


async def test_behind_a_proxy_the_forwarded_host_is_the_sites_own(
    client: AsyncClient, make_user: MakeUser
):
    # Railway's proxy may hand the app another Host and name the public one in
    # X-Forwarded-Host. A page cannot set that header for another site (it is not a simple
    # header, so the browser asks first and the site never allows it).
    await make_user("anna@example.com")
    public = "https://crm.example.com"
    proxied = {"Host": "app.railway.internal:8080", "X-Forwarded-Host": "crm.example.com"}
    assert (await log_in(client, {"Origin": public, **proxied})).status_code == 200
    assert (await log_in(client, {"Origin": "https://evil.example", **proxied})).status_code == 403


async def test_reading_is_not_checked(client: AsyncClient):
    # GET changes nothing: links and the page's own loading work from anywhere.
    response = await client.get("/api/health", headers={"Origin": "https://evil.example"})
    assert response.status_code == 200
