from httpx import AsyncClient

from tests.helpers import MakeUser, login


async def test_theme_defaults_to_blue(client: AsyncClient, make_user: MakeUser):
    await make_user("anna@example.com")
    await login(client, "anna@example.com")
    assert (await client.get("/api/auth/me")).json()["ui_theme"] == "blue"


async def test_user_changes_own_theme(client: AsyncClient, make_user: MakeUser):
    await make_user("anna@example.com", "employee")
    await login(client, "anna@example.com")
    response = await client.patch("/api/auth/preferences", json={"ui_theme": "dark"})
    assert response.status_code == 200
    assert response.json()["ui_theme"] == "dark"
    assert (await client.get("/api/auth/me")).json()["ui_theme"] == "dark"


async def test_theme_is_per_user(client: AsyncClient, make_user: MakeUser):
    tenant_id = await make_user("anna@example.com")
    await make_user("olga@example.com", "employee", tenant_id=tenant_id)
    await login(client, "anna@example.com")
    await client.patch("/api/auth/preferences", json={"ui_theme": "warm"})
    await client.post("/api/auth/logout")

    await login(client, "olga@example.com")
    assert (await client.get("/api/auth/me")).json()["ui_theme"] == "blue"


async def test_unknown_theme_is_rejected(client: AsyncClient, make_user: MakeUser):
    await make_user("anna@example.com")
    await login(client, "anna@example.com")
    response = await client.patch("/api/auth/preferences", json={"ui_theme": "pink"})
    assert response.status_code == 422


async def test_preferences_need_login(client: AsyncClient):
    response = await client.patch("/api/auth/preferences", json={"ui_theme": "dark"})
    assert response.status_code == 401
