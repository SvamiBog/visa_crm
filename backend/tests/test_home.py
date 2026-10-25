"""Главный экран (backlog 1.12): metrics, the nearest submissions, the month's funnel, the
queues and the money, each block only with the modules its data belongs to."""

from datetime import date, timedelta

from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from tests.helpers import MakeUser, give_cases, login, workspace_today


def day(n: int) -> str:
    return (workspace_today() + timedelta(days=n)).isoformat()


def last_month() -> date:
    """A day of the month before this one (its last day)."""
    return workspace_today().replace(day=1) - timedelta(days=1)


async def countries(client: AsyncClient) -> dict[str, str]:
    return {c["code"]: c["id"] for c in (await client.get("/api/reference/countries")).json()}


async def contact(client: AsyncClient, name: str) -> dict:
    response = await client.post("/api/contacts", json={"first_name": name})
    assert response.status_code == 201, response.text
    return response.json()


async def consult(client: AsyncClient, person: dict, held_on: str, decision=None) -> dict:
    response = await client.post(
        f"/api/contacts/{person['id']}/consultations",
        json={"held_on": held_on, "decision": decision},
    )
    assert response.status_code == 201, response.text
    return response.json()


async def open_cases(
    client: AsyncClient, codes: dict, person: dict, country: str, **body
) -> list[dict]:
    response = await client.post(
        f"/api/contacts/{person['id']}/cases",
        json={
            "destination_id": codes[country],
            "submission_country_id": codes["BY"],
            "applicants": [{"contact_id": person["id"]}],
        }
        | body,
    )
    assert response.status_code == 201, response.text
    return response.json()


async def ok(response) -> dict:
    assert response.status_code in (200, 201), response.text
    return response.json()


async def busy_workspace(client: AsyncClient, make_user: MakeUser) -> dict:
    """A family submitting in two days, a thinking client, a French application, a visa,
    a Polish queue, a client from last month and a debt to a registrar."""
    tenant_id = await make_user("anna@example.com")
    await login(client, "anna@example.com")
    codes = await countries(client)
    registrar = await ok(
        await client.post(
            "/api/dictionaries/registrars",
            json={"name": "Иван", "kind": "individual", "currency": "EUR"},
        )
    )

    anna = await contact(client, "Анна")
    talk = await consult(client, anna, day(0), "yes")
    family = await open_cases(
        client,
        codes,
        anna,
        "PL",
        consultation_id=talk["id"],
        registrar_id=registrar["id"],
        applicants=[{"contact_id": anna["id"]}, {"first_name": "Мария", "label": "дочь"}],
    )
    for case, price in zip(family, ("500", "300"), strict=True):
        await ok(await client.patch(f"/api/cases/{case['id']}", json={"submission_on": day(2)}))
        await ok(await client.patch(f"/api/cases/{case['id']}/finance", json={"price": price}))
    await ok(
        await client.patch(
            f"/api/cases/{family[0]['id']}/finance",
            json={"partner_terms": "percent", "partner_percent": "10"},
        )
    )
    await ok(
        await client.post(
            f"/api/cases/{family[0]['id']}/payments",
            json={"paid_on": day(0), "amount": "100", "currency": "BYN"},
        )
    )
    # The registrar: a 60 EUR talon against a 20 EUR payment — 40 EUR owed.
    await ok(
        await client.patch(
            f"/api/cases/{family[0]['id']}/finance",
            json={"registrar_fee_amount": "60", "registrar_fee_currency": "EUR"},
        )
    )
    await ok(
        await client.post(
            f"/api/registrars/{registrar['id']}/payments",
            json={"paid_on": day(0), "amount": "20", "method": "cash"},
        )
    )

    await consult(client, await contact(client, "Борис"), day(0), "thinking")

    vera = await contact(client, "Вера")
    talk = await consult(client, vera, day(0))
    [france] = await open_cases(client, codes, vera, "FR", consultation_id=talk["id"])
    await ok(await client.post(f"/api/cases/{france['id']}/attempts", json={"sent_on": day(-10)}))

    gleb = await contact(client, "Глеб")
    talk = await consult(client, gleb, day(0))
    [latvia] = await open_cases(client, codes, gleb, "LV", consultation_id=talk["id"])
    await ok(
        await client.patch(
            f"/api/cases/{latvia['id']}", json={"outcome": "visa", "decided_on": day(0)}
        )
    )

    await consult(client, await contact(client, "Даша"), last_month().isoformat(), "yes")

    [irina] = await open_cases(client, codes, await contact(client, "Ирина"), "PL")
    await ok(await client.post(f"/api/cases/{irina['id']}/queue", json={"queued_on": day(-30)}))
    [oleg] = await open_cases(client, codes, await contact(client, "Олег"), "PL")
    [entry] = await ok(
        await client.post(f"/api/cases/{oleg['id']}/queue", json={"queued_on": day(-100)})
    )
    await ok(
        await client.patch(
            f"/api/queue-entries/{entry['id']}",
            json={"letter_on": day(-10), "appointment_on": day(20)},
        )
    )
    return {"tenant_id": tenant_id, "codes": codes, "anna": anna, "family": family}


async def test_the_home_screen(client: AsyncClient, make_user: MakeUser):
    made = await busy_workspace(client, make_user)
    codes = made["codes"]
    home = await ok(await client.get("/api/home"))
    assert home["today"] == day(0)

    # Eight people, five in work (Gleb has his visa); four consultations this month, three
    # of them went to work (a «yes» or a case opened from it); six cases opened.
    assert home["clients"] == {
        "contacts": 8,
        "in_work": 5,
        "consultations": 4,
        "consultations_to_work": 3,
        "cases_opened": 6,
    }

    submissions = home["submissions"]
    assert (submissions["week"], submissions["next_on"]) == (2, day(2))
    # The family submitting together is one line; the balance is what they still owe.
    first, second = submissions["upcoming"]
    assert (first["contact_id"], first["others"], first["submission_on"]) == (
        made["anna"]["id"],
        1,
        day(2),
    )
    assert (first["destination_id"], first["stage"], first["balance"]) == (
        codes["PL"],
        "documents",
        "700.00",
    )
    assert (second["contact_name"], second["others"], second["balance"]) == ("Олег", 0, None)

    # Consultations of the month and how far they went.
    assert home["funnel"] == {
        "month": workspace_today().replace(day=1).isoformat(),
        "consultations": 4,
        "decided": 3,
        "queued": 3,
        "submitted": 1,
        "visas": 1,
    }
    month = last_month()
    last = await ok(await client.get(f"/api/home/funnel?month={month.strftime('%Y-%m')}"))
    assert last == {
        "month": month.replace(day=1).isoformat(),
        "consultations": 1,
        "decided": 1,
        "queued": 0,
        "submitted": 0,
        "visas": 0,
    }

    queues = home["queues"]
    assert (queues["waiting"], queues["open_attempts"], queues["average_days"]) == (1, 1, 90)
    by_country = {row["country_id"]: row for row in queues["countries"]}
    poland, france = by_country[codes["PL"]], by_country[codes["FR"]]
    assert (poland["waiting"], poland["average_days"], poland["next_deadline"]) == (1, 90, None)
    assert (france["open_attempts"], france["next_deadline"]) == (1, day(50))

    assert home["money"] == {
        "currency": "BYN",
        "debt": "700.00",
        "debtors": 2,
        "partners_due": "50.00",
        "partner_cases": 1,
        "registrars_due": [{"currency": "EUR", "amount": "40.00", "registrars": 1}],
    }

    bad = await client.get("/api/home/funnel?month=2026-13")
    assert bad.status_code == 422


async def test_blocks_follow_modules_and_workspaces(
    client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine
):
    made = await busy_workspace(client, make_user)
    client.cookies.clear()
    await make_user("olga@example.com", "employee", tenant_id=made["tenant_id"])

    async def give(*modules: str) -> dict:
        async with owner_engine.begin() as conn:
            await conn.execute(
                text("UPDATE users SET modules = :m WHERE email = 'olga@example.com'"),
                {"m": list(modules)},
            )
        return await ok(await client.get("/api/home"))

    await give_cases(owner_engine, "olga@example.com")
    await login(client, "olga@example.com")
    # Clients only: no case dates, queues or money.
    home = await give("clients")
    assert home["clients"]["in_work"] == 5
    assert home["submissions"] is home["queues"] is home["funnel"] is home["money"] is None
    funnel = await client.get(f"/api/home/funnel?month={workspace_today().strftime('%Y-%m')}")
    assert funnel.status_code == 403 and funnel.json()["detail"] == "module_disabled"
    # With cases but without money: no balances.
    home = await give("clients", "cases")
    assert home["money"] is None
    assert [row["balance"] for row in home["submissions"]["upcoming"]] == [None, None]
    assert home["funnel"]["visas"] == 1
    # With money too, but a balance with a registrar covers every case: the specialist's.
    home = await give("clients", "cases", "finances")
    assert home["money"]["debt"] != "0.00" and home["money"]["registrars_due"] == []
    home = await give()
    assert home["clients"] is home["submissions"] is home["money"] is None

    # Another workspace starts empty.
    client.cookies.clear()
    await make_user("boris@example.com")
    await login(client, "boris@example.com")
    other = await ok(await client.get("/api/home"))
    assert other["clients"]["contacts"] == 0 and other["submissions"]["upcoming"] == []
    assert other["queues"]["countries"] == [] and other["money"]["debt"] == "0.00"

    # The platform admin has no workspace.
    client.cookies.clear()
    await make_user("admin@example.com", "admin")
    await login(client, "admin@example.com")
    assert (await client.get("/api/home")).status_code == 403
