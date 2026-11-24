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
    await enable_two_factor(client, "anna-own-password-1")

    purposes = [(p["name"], p["kind"]) for p in (await client.get(f"{BASE}/purposes")).json()]
    channels = [c["name"] for c in (await client.get(f"{BASE}/channels")).json()]
    assert purposes == [
        ("Туризм", "tourism"),
        ("Гостевая", "visit"),
        ("Бизнес", "business"),
        ("Учёба", "study"),
    ]
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


async def test_purpose_has_a_kind_for_shared_lists(client: AsyncClient, make_user: MakeUser):
    await as_specialist(client, make_user)
    purpose_id = await add_purpose(client, "Работа в Польше")
    entry = (await client.get(f"{BASE}/purposes")).json()[0]
    assert entry["kind"] is None
    changed = await client.patch(f"{BASE}/purposes/{purpose_id}", json={"kind": "work"})
    assert changed.status_code == 200 and changed.json()["kind"] == "work"
    # Renaming keeps the kind; it is taken back only when sent as null.
    renamed = await client.patch(f"{BASE}/purposes/{purpose_id}", json={"name": "Работа"})
    assert renamed.json()["kind"] == "work"
    cleared = await client.patch(f"{BASE}/purposes/{purpose_id}", json={"kind": None})
    assert cleared.json()["kind"] is None
    bad = await client.patch(f"{BASE}/purposes/{purpose_id}", json={"kind": "holiday"})
    assert bad.status_code == 422
    # Lead channels have no kind.
    channel = await client.post(f"{BASE}/channels", json={"name": "Сайт", "kind": "work"})
    assert channel.status_code == 201 and "kind" not in channel.json()


# The lists the page adds to without choosing a place: a new entry goes to the end.
ORDERED_LISTS = [("purposes", {}), ("channels", {}), ("vault-systems", {"kind": "other"})]


@pytest.mark.parametrize(("kind", "extra"), ORDERED_LISTS)
async def test_a_new_entry_goes_to_the_end_of_its_list(
    client: AsyncClient, make_user: MakeUser, kind: str, extra: dict
):
    await as_specialist(client, make_user)
    first = (await client.post(f"{BASE}/{kind}", json={"name": "Б", **extra})).json()
    assert first["sort_order"] == 0
    await client.patch(f"{BASE}/{kind}/{first['id']}", json={"sort_order": 5})

    added = await client.post(f"{BASE}/{kind}", json={"name": "А", **extra})
    assert added.status_code == 201, added.text
    assert added.json()["sort_order"] == 6
    assert [e["name"] for e in (await client.get(f"{BASE}/{kind}")).json()] == ["Б", "А"]


@pytest.mark.parametrize(("kind", "extra"), ORDERED_LISTS)
async def test_a_list_at_the_highest_order_still_takes_new_entries(
    client: AsyncClient, make_user: MakeUser, kind: str, extra: dict
):
    """1.15.14: an entry at the highest order (999) left no «next» place, and the page's
    «last + 1» got a bare 422 instead of the entry or «name taken»."""
    await as_specialist(client, make_user)
    top = (await client.post(f"{BASE}/{kind}", json={"name": "Последняя", **extra})).json()
    raised = await client.patch(f"{BASE}/{kind}/{top['id']}", json={"sort_order": 999})
    assert raised.json()["sort_order"] == 999

    added = await client.post(f"{BASE}/{kind}", json={"name": "Новая", **extra})
    assert added.status_code == 201, added.text
    assert added.json()["sort_order"] == 999
    again = await client.post(f"{BASE}/{kind}", json={"name": "Новая", **extra})
    assert again.status_code == 409 and again.json()["detail"] == "name_taken"


async def test_employee_reads_but_does_not_edit(client: AsyncClient, make_user: MakeUser):
    tenant_id = await as_specialist(client, make_user)
    await add_purpose(client)
    client.cookies.clear()
    await make_user("olga@example.com", "employee", tenant_id=tenant_id)
    await login(client, "olga@example.com")
    assert len((await client.get(f"{BASE}/purposes")).json()) == 1
    response = await client.post(f"{BASE}/purposes", json={"name": "Учёба"})
    assert response.status_code == 403


async def test_employee_reads_partners_and_registrars_without_money(
    client: AsyncClient, make_user: MakeUser
):
    """An employee's forms need only the names: partners' terms, registrars' prices and
    balances, and prepayments are the specialist's (backlog Т.13)."""
    tenant_id = await as_specialist(client, make_user)
    tourism = await add_purpose(client)
    poland = await country_id(client, "PL")
    partner = (await client.post(f"{BASE}/partners", json={"name": "P", "contact": "@p"})).json()
    terms = [{"country_id": None, "terms_type": "percent", "percent": "10"}]
    response = await client.put(f"{BASE}/partners/{partner['id']}/terms", json=terms)
    assert response.status_code == 200, response.text
    registrar = (
        await client.post(
            f"{BASE}/registrars",
            json={"name": "R", "kind": "individual", "contact": "@r", "low_balance": "100"},
        )
    ).json()
    price = {
        "country_id": poland,
        "purpose_id": tourism,
        "hall": "standard",
        "amount": "60",
        "currency": "USD",
    }
    response = await client.put(f"{BASE}/registrars/{registrar['id']}/prices", json=[price])
    assert response.status_code == 200, response.text
    prepayment = {"country_id": poland, "purpose_id": None, "amount": "100"}
    assert (await client.put(f"{BASE}/prepayments", json=[prepayment])).status_code == 200
    # The specialist still gets everything.
    assert (await client.get(f"{BASE}/partners")).json()[0]["terms"]
    assert (await client.get(f"{BASE}/registrars")).json()[0]["prices"]

    client.cookies.clear()
    await make_user("olga@example.com", "employee", tenant_id=tenant_id)
    await login(client, "olga@example.com")
    for kind, item in (("partners", partner), ("registrars", registrar)):
        response = await client.get(f"{BASE}/{kind}")
        assert response.status_code == 200
        assert response.json() == [{"id": item["id"], "name": item["name"], "is_active": True}]
    response = await client.get(f"{BASE}/prepayments")
    assert response.status_code == 403
    assert response.json()["detail"] == "forbidden"


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


async def test_unused_partner_is_deleted_with_its_terms(client: AsyncClient, make_user: MakeUser):
    await as_specialist(client, make_user)
    partner = (await client.post(f"{BASE}/partners", json={"name": "Ольга"})).json()
    terms = [{"country_id": None, "terms_type": "percent", "percent": "10"}]
    response = await client.put(f"{BASE}/partners/{partner['id']}/terms", json=terms)
    assert response.status_code == 200, response.text

    response = await client.delete(f"{BASE}/partners/{partner['id']}")
    assert response.status_code == 204, response.text
    assert (await client.get(f"{BASE}/partners")).json() == []
    [event] = (await client.get("/api/audit")).json()["events"][:1]
    assert (event["action"], event["target_type"], event["target_id"]) == (
        "reference.deleted",
        "partner",
        partner["id"],
    )
    # Deleted is gone: a second try finds nothing.
    assert (await client.delete(f"{BASE}/partners/{partner['id']}")).status_code == 404


async def test_partner_named_on_a_client_card_is_kept(client: AsyncClient, make_user: MakeUser):
    """A client's cases count the partner's payout from the card, so a partner any card names
    stays (it can be switched off); once no card names it, it can be deleted."""
    await as_specialist(client, make_user)
    partner = (await client.post(f"{BASE}/partners", json={"name": "Ольга"})).json()
    contact = await client.post(
        "/api/contacts", json={"first_name": "Анна", "partner_id": partner["id"]}
    )
    assert contact.status_code == 201, contact.text

    response = await client.delete(f"{BASE}/partners/{partner['id']}")
    assert response.status_code == 409 and response.json()["detail"] == "partner_in_use"
    assert [p["id"] for p in (await client.get(f"{BASE}/partners")).json()] == [partner["id"]]

    cleared = await client.patch(f"/api/contacts/{contact.json()['id']}", json={"partner_id": None})
    assert cleared.status_code == 200, cleared.text
    assert (await client.delete(f"{BASE}/partners/{partner['id']}")).status_code == 204


async def test_only_the_specialist_deletes_her_own_partner(
    client: AsyncClient, make_user: MakeUser
):
    tenant_id = await as_specialist(client, make_user)
    partner = (await client.post(f"{BASE}/partners", json={"name": "Ольга"})).json()
    client.cookies.clear()

    await make_user("olga@example.com", "employee", tenant_id=tenant_id)
    await login(client, "olga@example.com")
    assert (await client.delete(f"{BASE}/partners/{partner['id']}")).status_code == 403
    client.cookies.clear()

    await as_specialist(client, make_user, "bella@example.com")
    assert (await client.delete(f"{BASE}/partners/{partner['id']}")).status_code == 404
    client.cookies.clear()

    await login(client, "anna@example.com")
    assert [p["id"] for p in (await client.get(f"{BASE}/partners")).json()] == [partner["id"]]


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


async def add_registrar(client: AsyncClient, name: str = "Иван") -> dict:
    response = await client.post(f"{BASE}/registrars", json={"name": name, "kind": "individual"})
    assert response.status_code == 201, response.text
    return response.json()


async def test_unused_registrar_is_deleted_with_its_prices(
    client: AsyncClient, make_user: MakeUser
):
    await as_specialist(client, make_user)
    registrar = await add_registrar(client)
    prices = [
        {
            "country_id": await country_id(client, "PL"),
            "purpose_id": None,
            "hall": "standard",
            "amount": "60",
            "currency": "USD",
        }
    ]
    response = await client.put(f"{BASE}/registrars/{registrar['id']}/prices", json=prices)
    assert response.status_code == 200, response.text

    response = await client.delete(f"{BASE}/registrars/{registrar['id']}")
    assert response.status_code == 204, response.text
    assert (await client.get(f"{BASE}/registrars")).json() == []
    [event] = (await client.get("/api/audit")).json()["events"][:1]
    assert (event["action"], event["target_type"], event["target_id"]) == (
        "reference.deleted",
        "registrar",
        registrar["id"],
    )
    # Deleted is gone: a second try finds nothing.
    assert (await client.delete(f"{BASE}/registrars/{registrar['id']}")).status_code == 404


async def test_registrar_on_a_case_is_kept(client: AsyncClient, make_user: MakeUser):
    """A case counts the talon by its registrar, so a registrar any case names stays (it can
    be switched off); once no case names it, it can be deleted."""
    await as_specialist(client, make_user)
    registrar = await add_registrar(client)
    contact = (await client.post("/api/contacts", json={"first_name": "Анна"})).json()
    opened = await client.post(
        f"/api/contacts/{contact['id']}/cases",
        json={
            "destination_id": await country_id(client, "PL"),
            "submission_country_id": await country_id(client, "BY"),
            "registrar_id": registrar["id"],
            "applicants": [{"contact_id": contact["id"]}],
        },
    )
    assert opened.status_code == 201, opened.text

    response = await client.delete(f"{BASE}/registrars/{registrar['id']}")
    assert response.status_code == 409 and response.json()["detail"] == "registrar_in_use"
    assert [r["id"] for r in (await client.get(f"{BASE}/registrars")).json()] == [registrar["id"]]

    case_id = opened.json()[0]["id"]
    cleared = await client.patch(f"/api/cases/{case_id}", json={"registrar_id": None})
    assert cleared.status_code == 200, cleared.text
    assert (await client.delete(f"{BASE}/registrars/{registrar['id']}")).status_code == 204


async def test_registrar_with_payments_is_kept(client: AsyncClient, make_user: MakeUser):
    """Payments to a registrar are the specialist's money records: deleting the registrar
    would take them along, so it can only be switched off."""
    await as_specialist(client, make_user)
    registrar = await add_registrar(client)
    paid = await client.post(
        f"/api/registrars/{registrar['id']}/payments",
        json={"paid_on": "2026-05-01", "amount": "300", "method": "transfer"},
    )
    assert paid.status_code == 201, paid.text

    response = await client.delete(f"{BASE}/registrars/{registrar['id']}")
    assert response.status_code == 409 and response.json()["detail"] == "registrar_in_use"
    assert [r["id"] for r in (await client.get(f"{BASE}/registrars")).json()] == [registrar["id"]]


async def test_only_the_specialist_deletes_her_own_registrar(
    client: AsyncClient, make_user: MakeUser
):
    tenant_id = await as_specialist(client, make_user)
    registrar = await add_registrar(client)
    client.cookies.clear()

    await make_user("olga@example.com", "employee", tenant_id=tenant_id)
    await login(client, "olga@example.com")
    assert (await client.delete(f"{BASE}/registrars/{registrar['id']}")).status_code == 403
    client.cookies.clear()

    await as_specialist(client, make_user, "bella@example.com")
    assert (await client.delete(f"{BASE}/registrars/{registrar['id']}")).status_code == 404
    client.cookies.clear()

    await login(client, "anna@example.com")
    assert [r["id"] for r in (await client.get(f"{BASE}/registrars")).json()] == [registrar["id"]]


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
