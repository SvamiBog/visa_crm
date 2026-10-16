"""Demo data for the staging stand: it fills empty workspaces, works through the API like
real data and never runs in production."""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.db import scope_to_system
from app.core.demo import (
    ADMIN_EMAIL,
    DemoRefused,
    DemoSettings,
    already_seeded,
    check_settings,
    seed,
    specialist_email,
)
from tests.helpers import TOTP_SECRET, login

PASSWORD = "demo-password-42"


def test_demo_data_is_refused_outside_the_stand():
    with pytest.raises(DemoRefused):
        check_settings("production", PASSWORD, TOTP_SECRET)
    with pytest.raises(DemoRefused):
        check_settings("staging", "short", TOTP_SECRET)
    with pytest.raises(DemoRefused):
        check_settings("staging", PASSWORD, "not base32!")
    check_settings("staging", PASSWORD, TOTP_SECRET)


async def test_demo_workspaces_work_like_real_ones(
    client: AsyncClient, sessionmaker: async_sessionmaker[AsyncSession]
):
    async with sessionmaker() as session:
        scope_to_system(session)
        assert not await already_seeded(session)
        totals = await seed(session, DemoSettings(PASSWORD, TOTP_SECRET, specialists=2, clients=25))
        await session.commit()
        assert await already_seeded(session)
    assert totals["specialists"] == 2 and totals["clients"] >= 40 and totals["cases"] > 0

    assert (await login(client, specialist_email(1), PASSWORD)).status_code == 200
    index = (await client.post("/api/contacts/search", json={})).json()
    assert sum(index["counts"].values()) > 0 and index["counts"]["all"] >= 20
    own = {c["id"] for c in index["items"]}
    # Every card opens, and a vault password decrypts.
    contact = index["items"][0]["id"]
    assert (await client.get(f"/api/contacts/{contact}")).status_code == 200
    [mailbox, *_] = (await client.get("/api/vault/own")).json()
    shown = await client.post(f"/api/vault/entries/{mailbox['id']}/reveal")
    assert shown.status_code == 200 and len(shown.json()["password"]) >= 12
    assert (await client.get("/api/bookings/deadlines")).status_code == 200
    assert (await client.get("/api/queues/summary")).status_code == 200

    # The other demo specialist sees only their own clients.
    client.cookies.clear()
    await login(client, specialist_email(2), PASSWORD)
    other = {c["id"] for c in (await client.post("/api/contacts/search", json={})).json()["items"]}
    assert other and not other & own

    client.cookies.clear()
    assert (await login(client, ADMIN_EMAIL, PASSWORD)).status_code == 200
    assert len((await client.get("/api/admin/specialists")).json()) == 2
