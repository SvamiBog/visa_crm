import pytest
from fastapi import Depends
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.api.deps import require_module
from app.core.modules import DEFAULT_MODULES, MODULES, normalize_modules
from app.main import create_app
from tests.support.helpers import MakeUser, login


async def as_admin(client: AsyncClient, make_user: MakeUser) -> None:
    await make_user("root@example.com", role="admin")
    await login(client, "root@example.com")


def test_registry_matches_prd_module_map():
    assert len(MODULES) == 23
    assert DEFAULT_MODULES == (
        "clients",
        "cases",
        "vault",
        "finances",
        "tasks",
        "country_rules",
        "analytics",
    )


def test_normalize_orders_and_deduplicates():
    assert normalize_modules(["analytics", "clients", "clients"]) == ["clients", "analytics"]
    with pytest.raises(ValueError):
        normalize_modules(["teleport"])


async def test_new_specialist_gets_core_modules_and_tax(client: AsyncClient, make_user: MakeUser):
    await as_admin(client, make_user)
    created = await client.post(
        "/api/admin/specialists",
        json={"name": "Anna", "email": "anna@example.com", "tax_rate": "10"},
    )
    specialist = created.json()["specialist"]
    assert specialist["modules"] == list(DEFAULT_MODULES)
    assert specialist["tax_rate"] == "10.00"


@pytest.mark.parametrize(
    "body",
    [{"modules": ["teleport"]}, {"tax_rate": "-1"}, {"tax_rate": "100.5"}, {"name": ""}],
)
async def test_invalid_updates_are_rejected(client: AsyncClient, make_user: MakeUser, body: dict):
    await as_admin(client, make_user)
    tenant_id = await make_user("anna@example.com")
    response = await client.patch(f"/api/admin/specialists/{tenant_id}", json=body)
    assert response.status_code == 422


async def test_admin_updates_specialist_and_it_is_logged(client: AsyncClient, make_user: MakeUser):
    await as_admin(client, make_user)
    tenant_id = await make_user("anna@example.com")
    updated = await client.patch(
        f"/api/admin/specialists/{tenant_id}",
        json={"name": " Anna Visa ", "tax_rate": "10", "modules": ["slots", "clients"]},
    )
    body = updated.json()
    assert (body["name"], body["tax_rate"], body["modules"]) == (
        "Anna Visa",
        "10.00",
        ["clients", "slots"],
    )
    event = (await client.get("/api/admin/audit")).json()["events"][0]
    assert event["action"] == "admin.specialist_updated"
    assert event["details"] == {
        "changed": ["modules", "name", "tax_rate"],
        "modules_on": ["slots"],
        "modules_off": ["cases", "vault", "finances", "tasks", "country_rules", "analytics"],
    }


async def test_modules_list_for_the_ui(client: AsyncClient, make_user: MakeUser):
    await make_user("anna@example.com")
    await login(client, "anna@example.com")
    modules = (await client.get("/api/modules")).json()
    assert [m["key"] for m in modules][:2] == ["clients", "cases"]


@pytest.fixture
async def module_client(owner_engine: AsyncEngine, client: AsyncClient):
    """The app plus a test-only endpoint guarded by the "clients" module."""
    app = create_app()

    @app.get("/api/test-clients", dependencies=[Depends(require_module("clients"))])
    async def clients_only() -> dict[str, bool]:
        return {"ok": True}

    app.router.routes.insert(0, app.router.routes.pop())  # before the frontend catch-all

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as c:
        yield c


async def set_modules(engine: AsyncEngine, sql: str, **params: object) -> None:
    async with engine.begin() as conn:
        await conn.execute(text(sql), params)


async def test_module_guard(
    module_client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine
):
    tenant_id = await make_user("anna@example.com")
    await make_user("staff@example.com", role="employee", tenant_id=tenant_id)
    await set_modules(owner_engine, "UPDATE tenants SET modules = '{clients}'")

    await login(module_client, "anna@example.com")
    assert (await module_client.get("/api/test-clients")).status_code == 200
    await module_client.post("/api/auth/logout")

    # An employee needs the module given to them as well.
    await login(module_client, "staff@example.com")
    denied = await module_client.get("/api/test-clients")
    assert denied.json() == {"detail": "module_disabled"}
    await set_modules(
        owner_engine, "UPDATE users SET modules = '{clients}' WHERE role = 'employee'"
    )
    assert (await module_client.get("/api/test-clients")).status_code == 200
    assert (await module_client.get("/api/workspace")).json()["modules"] == ["clients"]

    # Turned off for the workspace: gone for the employee too.
    await set_modules(
        owner_engine, "UPDATE tenants SET modules = '{}' WHERE id = :id", id=tenant_id
    )
    assert (await module_client.get("/api/test-clients")).status_code == 403
    assert (await module_client.get("/api/workspace")).json()["modules"] == []


async def test_admin_has_no_modules(module_client: AsyncClient, make_user: MakeUser):
    await make_user("root@example.com", role="admin")
    await login(module_client, "root@example.com")
    assert (await module_client.get("/api/test-clients")).status_code == 403


async def test_a_module_turned_off_closes_its_lists_and_leaves_what_other_forms_read(
    client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine
):
    """A module the admin turned off is closed in the API, not only in the menu
    (docs/decisions.md → «Модули в API»): the directions builder writes under
    «country_rules», partners, registrars and prepayments under «finances», vault systems
    under «vault». What the forms of the modules still on read stays open: countries,
    operators and directions (the case form), partners (the contact form), registrars
    (the case form), vault systems (the account of a booking), prepayments (their list),
    currencies and tax («Настройки», the consultation's fee)."""
    tenant_id = await make_user("anna@example.com")
    await login(client, "anna@example.com")
    codes = {c["code"]: c["id"] for c in (await client.get("/api/reference/countries")).json()}
    rule = (await client.get("/api/reference/rules")).json()[0]
    centers = await client.post(
        f"/api/reference/rules/{rule['id']}/visa-centers", json={"cities": ["Гродно"]}
    )
    center = next(c for c in centers.json()["visa_centers"] if c["city_ru"] == "Гродно")
    partner = (await client.post("/api/dictionaries/partners", json={"name": "Партнёр"})).json()
    registrar = (
        await client.post("/api/dictionaries/registrars", json={"name": "Рег", "kind": "company"})
    ).json()
    system = (
        await client.post(
            "/api/dictionaries/vault-systems", json={"name": "VFS", "kind": "visa_center"}
        )
    ).json()
    await set_modules(
        owner_engine,
        "UPDATE tenants SET modules = '{clients,cases,tasks,analytics}' WHERE id = :id",
        id=tenant_id,
    )

    closed = [
        (
            "POST",
            "/api/reference/rules",
            {
                "destination_ids": [codes["PL"]],
                "submission_id": codes["BY"],
                "booking_type": "direct_slot",
            },
        ),
        ("PATCH", f"/api/reference/rules/{rule['id']}", {"is_active": False}),
        ("DELETE", f"/api/reference/rules/{rule['id']}", None),
        ("POST", f"/api/reference/rules/{rule['id']}/visa-centers", {"cities": ["Брест"]}),
        ("PATCH", f"/api/reference/visa-centers/{center['id']}", {"is_active": False}),
        ("DELETE", f"/api/reference/visa-centers/{center['id']}", None),
        ("POST", "/api/dictionaries/partners", {"name": "Другой"}),
        ("PATCH", f"/api/dictionaries/partners/{partner['id']}", {"name": "Новый"}),
        ("PUT", f"/api/dictionaries/partners/{partner['id']}/terms", []),
        ("DELETE", f"/api/dictionaries/partners/{partner['id']}", None),
        ("POST", "/api/dictionaries/registrars", {"name": "Другой", "kind": "company"}),
        ("PATCH", f"/api/dictionaries/registrars/{registrar['id']}", {"name": "Новый"}),
        ("PUT", f"/api/dictionaries/registrars/{registrar['id']}/prices", []),
        ("DELETE", f"/api/dictionaries/registrars/{registrar['id']}", None),
        ("PUT", "/api/dictionaries/prepayments", []),
        ("POST", "/api/dictionaries/vault-systems", {"name": "TLS", "kind": "visa_center"}),
        ("PATCH", f"/api/dictionaries/vault-systems/{system['id']}", {"name": "VFS Global"}),
    ]
    answers = {}
    for method, url, body in closed:
        response = await client.request(method, url, json=body)
        answer = response.json() if response.content else None
        detail = answer.get("detail") if isinstance(answer, dict) else None
        answers[f"{method} {url}"] = (response.status_code, detail)
    assert answers == {route: (403, "module_disabled") for route in answers}

    read = [
        "/api/reference/countries",
        "/api/reference/operators",
        "/api/reference/rules",
        "/api/dictionaries/partners",
        "/api/dictionaries/registrars",
        "/api/dictionaries/prepayments",
        "/api/dictionaries/vault-systems",
        "/api/dictionaries/money",
    ]
    assert {url: (await client.get(url)).status_code for url in read} == dict.fromkeys(read, 200)
    taxed = await client.patch("/api/dictionaries/money", json={"tax_rate": "6"})
    assert taxed.status_code == 200, taxed.text
