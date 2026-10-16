import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.db import scope_to_tenant
from app.models import Registrar
from tests.helpers import MakeUser, enable_two_factor, login

BASE = "/api/dictionaries"


async def country_id(client: AsyncClient, code: str) -> str:
    countries = (await client.get("/api/reference/countries")).json()
    return next(c["id"] for c in countries if c["code"] == code)


async def as_specialist(client: AsyncClient, make_user: MakeUser, email="anna@example.com"):
    tenant_id = await make_user(email)
    await login(client, email)
    return tenant_id


async def add_purpose(client: AsyncClient, name: str = "Туризм") -> str:
    response = await client.post(f"{BASE}/purposes", json={"name": name})
    assert response.status_code == 201, response.text
    return response.json()["id"]


async def test_new_specialist_gets_default_lists(client: AsyncClient, make_user: MakeUser):
    await make_user("root@example.com", role="admin")
    await login(client, "root@example.com")
    created = await client.post(
        "/api/admin/specialists", json={"name": "Anna", "email": "anna@example.com"}
    )
    temporary = created.json()["temporary_password"]
    client.cookies.clear()
    await login(client, "anna@example.com", temporary)
    await client.post(
        "/api/auth/change-password",
        json={"current_password": temporary, "new_password": "anna-own-password-1"},
    )
    await enable_two_factor(client)

    purposes = [p["name"] for p in (await client.get(f"{BASE}/purposes")).json()]
    channels = [c["name"] for c in (await client.get(f"{BASE}/channels")).json()]
    assert purposes == ["Туризм", "Гостевая", "Бизнес", "Учёба"]
    assert channels == ["Instagram", "Сарафан", "Партнёр", "Друзья", "Реклама"]
    systems = (await client.get(f"{BASE}/vault-systems")).json()
    assert [(s["name"], s["kind"]) for s in systems][:2] == [
        ("Почта", "email"),
        ("VFS Global", "visa_center"),
    ]
    assert len(systems) == 6


@pytest.mark.parametrize("kind", ["purposes", "channels"])
async def test_specialist_keeps_simple_lists(client: AsyncClient, make_user: MakeUser, kind: str):
    await as_specialist(client, make_user)
    created = await client.post(f"{BASE}/{kind}", json={"name": "Работа"})
    assert created.status_code == 201
    item = created.json()
    duplicate = await client.post(f"{BASE}/{kind}", json={"name": "Работа"})
    assert duplicate.status_code == 409 and duplicate.json()["detail"] == "name_taken"

    renamed = await client.patch(f"{BASE}/{kind}/{item['id']}", json={"name": "Работа (D)"})
    assert renamed.json()["name"] == "Работа (D)"
    off = await client.patch(f"{BASE}/{kind}/{item['id']}", json={"is_active": False})
    assert off.json()["is_active"] is False
    # Switched-off entries stay in the list (old cases use them) but are marked.
    assert [i["is_active"] for i in (await client.get(f"{BASE}/{kind}")).json()] == [False]


async def test_employee_reads_but_does_not_edit(client: AsyncClient, make_user: MakeUser):
    tenant_id = await as_specialist(client, make_user)
    await add_purpose(client)
    client.cookies.clear()
    await make_user("olga@example.com", "employee", tenant_id=tenant_id)
    await login(client, "olga@example.com")
    assert len((await client.get(f"{BASE}/purposes")).json()) == 1
    response = await client.post(f"{BASE}/purposes", json={"name": "Учёба"})
    assert response.status_code == 403


async def test_admin_has_no_workspace_lists(client: AsyncClient, make_user: MakeUser):
    await make_user("root@example.com", role="admin")
    await login(client, "root@example.com")
    assert (await client.get(f"{BASE}/purposes")).status_code == 403


async def test_workspaces_do_not_see_each_other(client: AsyncClient, make_user: MakeUser):
    await as_specialist(client, make_user)
    purpose = await add_purpose(client)
    await client.post(f"{BASE}/registrars", json={"name": "Reg A", "kind": "company"})
    client.cookies.clear()

    await as_specialist(client, make_user, "bella@example.com")
    assert (await client.get(f"{BASE}/purposes")).json() == []
    assert (await client.get(f"{BASE}/registrars")).json() == []
    # Another workspace's purpose cannot be used in a price.
    registrar = (
        await client.post(f"{BASE}/registrars", json={"name": "Reg B", "kind": "individual"})
    ).json()
    poland = await country_id(client, "PL")
    response = await client.put(
        f"{BASE}/registrars/{registrar['id']}/prices",
        json=[
            {
                "country_id": poland,
                "purpose_id": purpose,
                "hall": "standard",
                "amount": "50",
                "currency": "USD",
            }
        ],
    )
    assert response.status_code == 404


async def test_partner_terms_by_country(client: AsyncClient, make_user: MakeUser):
    await as_specialist(client, make_user)
    partner = (await client.post(f"{BASE}/partners", json={"name": "Ольга (легализатор)"})).json()
    poland = await country_id(client, "PL")
    france = await country_id(client, "FR")
    terms = [
        {"country_id": None, "terms_type": "percent", "percent": "10"},
        {"country_id": poland, "terms_type": "fixed", "amount": "50", "currency": "EUR"},
        {"country_id": france, "terms_type": "unspecified"},
    ]
    response = await client.put(f"{BASE}/partners/{partner['id']}/terms", json=terms)
    assert response.status_code == 200, response.text
    saved = {t["country_id"]: t for t in response.json()["terms"]}
    assert saved[None]["percent"] == "10.00"
    assert saved[poland]["amount"] == "50.00" and saved[poland]["currency"] == "EUR"
    assert saved[france]["percent"] is None and saved[france]["amount"] is None

    # Replacing the list drops terms that are not sent.
    response = await client.put(f"{BASE}/partners/{partner['id']}/terms", json=terms[:1])
    assert len(response.json()["terms"]) == 1


@pytest.mark.parametrize(
    "terms",
    [
        [{"country_id": None, "terms_type": "percent"}],  # percent without a value
        [{"country_id": None, "terms_type": "percent", "percent": "120"}],
        [{"country_id": None, "terms_type": "fixed", "amount": "50"}],  # no currency
        [{"country_id": None, "terms_type": "fixed", "amount": "50", "currency": "RUB"}],
        [
            {"country_id": None, "terms_type": "none"},
            {"country_id": None, "terms_type": "unspecified"},
        ],  # the same country twice
    ],
)
async def test_invalid_partner_terms_are_rejected(
    client: AsyncClient, make_user: MakeUser, terms: list[dict]
):
    await as_specialist(client, make_user)
    partner = (await client.post(f"{BASE}/partners", json={"name": "P"})).json()
    response = await client.put(f"{BASE}/partners/{partner['id']}/terms", json=terms)
    assert response.status_code == 422


async def test_registrar_price_list(client: AsyncClient, make_user: MakeUser):
    await as_specialist(client, make_user)
    tourism = await add_purpose(client)
    registrar = (
        await client.post(
            f"{BASE}/registrars",
            json={"name": "Иван", "kind": "individual", "contact": "@ivan"},
        )
    ).json()
    poland = await country_id(client, "PL")
    prices = [
        {
            "country_id": poland,
            "purpose_id": tourism,
            "hall": "standard",
            "amount": "60",
            "currency": "USD",
        },
        {
            "country_id": poland,
            "purpose_id": tourism,
            "hall": "premium",
            "amount": "90.5",
            "currency": "USD",
        },
        {
            "country_id": poland,
            "purpose_id": None,
            "hall": "standard",
            "amount": "150",
            "currency": "BYN",
        },
    ]
    response = await client.put(f"{BASE}/registrars/{registrar['id']}/prices", json=prices)
    assert response.status_code == 200, response.text
    amounts = sorted(p["amount"] for p in response.json()["prices"])
    assert amounts == ["150.00", "60.00", "90.50"]

    twice = await client.put(
        f"{BASE}/registrars/{registrar['id']}/prices", json=[prices[0], prices[0]]
    )
    assert twice.status_code == 422
    free = await client.put(
        f"{BASE}/registrars/{registrar['id']}/prices", json=[{**prices[0], "amount": "0"}]
    )
    assert free.status_code == 422


async def test_prepayments(client: AsyncClient, make_user: MakeUser):
    await as_specialist(client, make_user)
    tourism = await add_purpose(client)
    poland = await country_id(client, "PL")
    france = await country_id(client, "FR")
    body = [
        {"country_id": poland, "purpose_id": tourism, "amount": "100"},
        {"country_id": france, "purpose_id": None, "amount": "150.50"},
    ]
    response = await client.put(f"{BASE}/prepayments", json=body)
    assert response.status_code == 200, response.text
    assert sorted(p["amount"] for p in response.json()) == ["100.00", "150.50"]
    assert len((await client.get(f"{BASE}/prepayments")).json()) == 2

    unknown = await client.put(
        f"{BASE}/prepayments",
        json=[{"country_id": str(uuid.uuid4()), "purpose_id": None, "amount": "10"}],
    )
    assert unknown.status_code == 404


async def test_money_settings(client: AsyncClient, make_user: MakeUser):
    await as_specialist(client, make_user)
    money = (await client.get(f"{BASE}/money")).json()
    assert money["base_currency"] == "BYN"
    assert money["payment_currencies"] == ["BYN", "PLN", "USD", "EUR"]
    assert money["rate_source"] == "nbrb"

    changed = await client.patch(f"{BASE}/money", json={"payment_currencies": ["USD", "EUR"]})
    # The base currency is always accepted, and the order is stable.
    assert changed.json()["payment_currencies"] == ["BYN", "USD", "EUR"]
    bad = await client.patch(f"{BASE}/money", json={"payment_currencies": ["RUB"]})
    assert bad.status_code == 422


async def test_changes_are_in_the_workspace_log(client: AsyncClient, make_user: MakeUser):
    await as_specialist(client, make_user)
    await add_purpose(client, "Учёба")
    events = (await client.get("/api/audit")).json()["events"]
    assert (events[0]["action"], events[0]["target_type"]) == ("reference.created", "visa_purpose")


async def test_rls_hides_other_workspace_rows(
    client: AsyncClient,
    make_user: MakeUser,
    sessionmaker: async_sessionmaker[AsyncSession],
):
    tenant_a = await as_specialist(client, make_user)
    await client.post(f"{BASE}/registrars", json={"name": "Reg A", "kind": "company"})
    tenant_b = await make_user("bella@example.com")
    async with sessionmaker() as session:
        scope_to_tenant(session, tenant_b)
        assert (await session.scalars(select(Registrar))).all() == []
    async with sessionmaker() as session:
        scope_to_tenant(session, tenant_a)
        assert [r.name for r in await session.scalars(select(Registrar))] == ["Reg A"]
