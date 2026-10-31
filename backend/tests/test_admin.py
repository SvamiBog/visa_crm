from httpx import AsyncClient

from tests.helpers import PASSWORD, MakeUser, enable_two_factor, login

NEW_PASSWORD = "my-own-password-1"


async def as_admin(client: AsyncClient, make_user: MakeUser) -> None:
    await make_user("root@example.com", role="admin")
    assert (await login(client, "root@example.com")).status_code == 200


async def create_specialist(client: AsyncClient, email: str = "anna@example.com") -> dict:
    response = await client.post(
        "/api/admin/specialists",
        json={"name": "Anna Visa", "email": email, "base_currency": "PLN"},
    )
    assert response.status_code == 201, response.text
    return response.json()


async def test_admin_creates_specialist_who_must_change_password(
    client: AsyncClient, make_user: MakeUser
):
    await as_admin(client, make_user)
    created = await create_specialist(client, "Anna@Example.com")
    assert created["specialist"]["email"] == "anna@example.com"
    assert created["specialist"]["base_currency"] == "PLN"
    temporary = created["temporary_password"]
    assert len(temporary) >= 12

    listed = (await client.get("/api/admin/specialists")).json()
    assert [s["email"] for s in listed] == ["anna@example.com"]

    client.cookies.clear()
    me = (await login(client, "anna@example.com", temporary)).json()
    assert me["must_change_password"] is True
    await client.post(
        "/api/auth/change-password",
        json={"current_password": temporary, "new_password": NEW_PASSWORD},
    )
    await enable_two_factor(client)
    assert (await client.get("/api/workspace")).json() == {
        "name": "Anna Visa",
        "base_currency": "PLN",
        "tax_rate": "0.00",
        "timezone": "Europe/Minsk",
        "modules": ["clients", "cases", "vault", "finances", "tasks", "country_rules", "analytics"],
        "file_storage": True,  # tests keep files in memory
        "subscription": {"status": "none", "next_due_on": None, "overdue_days": 0},
    }


async def test_duplicate_email_is_rejected(client: AsyncClient, make_user: MakeUser):
    await as_admin(client, make_user)
    await create_specialist(client)
    duplicate = await client.post(
        "/api/admin/specialists", json={"name": "Again", "email": "anna@example.com"}
    )
    assert duplicate.status_code == 409
    assert duplicate.json() == {"detail": "email_taken"}


async def test_blocking_signs_specialist_out_and_stops_login(
    client: AsyncClient, make_user: MakeUser
):
    await as_admin(client, make_user)
    tenant_id = await make_user("anna@example.com")
    admin_cookies = dict(client.cookies)

    client.cookies.clear()
    await login(client, "anna@example.com")
    anna_cookies = dict(client.cookies)

    client.cookies = admin_cookies
    blocked = await client.post(f"/api/admin/specialists/{tenant_id}/block")
    assert blocked.json()["is_active"] is False

    client.cookies = anna_cookies
    assert (await client.get("/api/auth/me")).status_code == 401
    assert (await login(client, "anna@example.com")).json() == {"detail": "account_disabled"}

    client.cookies = admin_cookies
    await client.post(f"/api/admin/specialists/{tenant_id}/unblock")
    assert (await login(client, "anna@example.com")).status_code == 200


async def test_reset_password_issues_new_temporary_password(
    client: AsyncClient, make_user: MakeUser
):
    await as_admin(client, make_user)
    tenant_id = await make_user("anna@example.com")
    reset = await client.post(f"/api/admin/specialists/{tenant_id}/reset-password")
    temporary = reset.json()["temporary_password"]

    client.cookies.clear()
    assert (await login(client, "anna@example.com", PASSWORD)).status_code == 401
    me = (await login(client, "anna@example.com", temporary)).json()
    # A new temporary password also turns two-factor login off (lost phone).
    assert me["must_change_password"] is True
    assert me["two_factor_enabled"] is False


async def test_specialist_cannot_use_admin_api(client: AsyncClient, make_user: MakeUser):
    await make_user("anna@example.com")
    await login(client, "anna@example.com")
    assert (await client.get("/api/admin/specialists")).status_code == 403
    created = await client.post(
        "/api/admin/specialists", json={"name": "Sneaky", "email": "sneaky@example.com"}
    )
    assert created.status_code == 403


async def test_admin_api_needs_login(client: AsyncClient):
    assert (await client.get("/api/admin/specialists")).status_code == 401
