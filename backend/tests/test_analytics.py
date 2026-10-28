"""Аналитика (backlog 1.13): reports of a period computed from the cards — channels,
months, countries and results, visa centers, queues, money and applicants."""

import uuid

from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from tests.helpers import MakeUser, login

DICT = "/api/dictionaries"
PERIOD = {"start": "2026-05-01", "end": "2026-06-30"}


async def ok(response) -> dict:
    assert response.status_code in (200, 201, 204), response.text
    return response.json() if response.status_code != 204 else {}


async def countries(client: AsyncClient) -> dict[str, str]:
    return {c["code"]: c["id"] for c in (await client.get("/api/reference/countries")).json()}


async def contact(client: AsyncClient, name: str, **fields) -> dict:
    return await ok(await client.post("/api/contacts", json={"first_name": name, **fields}))


async def consult(client: AsyncClient, person: dict, held_on: str, **fields) -> dict:
    return await ok(
        await client.post(
            f"/api/contacts/{person['id']}/consultations", json={"held_on": held_on, **fields}
        )
    )


async def open_cases(client: AsyncClient, codes: dict, person: dict, country: str, **body):
    return await ok(
        await client.post(
            f"/api/contacts/{person['id']}/cases",
            json={
                "destination_id": codes[country],
                "submission_country_id": codes["BY"],
                "applicants": [{"contact_id": person["id"]}],
            }
            | body,
        )
    )


async def patch(client: AsyncClient, case: dict, **body) -> dict:
    return await ok(await client.patch(f"/api/cases/{case['id']}", json=body))


async def price(client: AsyncClient, case: dict, amount: str, **terms) -> None:
    await ok(await client.patch(f"/api/cases/{case['id']}/finance", json={"price": amount} | terms))


async def center(
    owner_engine: AsyncEngine, tenant_id: uuid.UUID, codes: dict, country: str, city: str
) -> str:
    """A city of the workspace's direction «country × Belarus»."""
    center_id = uuid.uuid4()
    async with owner_engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO visa_centers (id, tenant_id, rule_id, city_ru) "
                "SELECT :id, tenant_id, id, :city FROM country_rules "
                "WHERE tenant_id = :tenant AND destination_id = :dest AND submission_id = :by"
            ),
            {
                "id": center_id,
                "tenant": tenant_id,
                "city": city,
                "dest": codes[country],
                "by": codes["BY"],
            },
        )
    return str(center_id)


async def workspace(client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine) -> dict:
    """May–June 2026: three consultations from two channels, two visas and a refusal, a
    client who did not go, a Polish queue and a French application."""
    tenant_id = await make_user("anna@example.com")
    await login(client, "anna@example.com")
    codes = await countries(client)
    insta = await ok(await client.post(f"{DICT}/channels", json={"name": "Instagram"}))
    word = await ok(await client.post(f"{DICT}/channels", json={"name": "Сарафан"}))
    peter = await ok(await client.post(f"{DICT}/partners", json={"name": "Пётр"}))
    minsk = await center(owner_engine, tenant_id, codes, "PL", "Минск")

    anna = await contact(client, "Анна", lead_channel_id=insta["id"])
    talk = await consult(client, anna, "2026-05-10", decision="yes", destination_ids=[codes["PL"]])
    [poland] = await open_cases(
        client, codes, anna, "PL", consultation_id=talk["id"], visa_center_id=minsk
    )
    await patch(client, poland, submission_on="2026-05-20")
    await patch(
        client,
        poland,
        outcome="visa",
        decided_on="2026-06-03",
        visa_entries="multiple",
        visa_valid_from="2026-06-05",
        visa_valid_to="2027-06-04",
    )
    await price(client, poland, "500")
    await ok(
        await client.post(
            f"/api/cases/{poland['id']}/payments",
            json={"paid_on": "2026-05-20", "amount": "500", "currency": "BYN"},
        )
    )

    boris = await contact(client, "Борис", lead_channel_id=insta["id"])
    await consult(client, boris, "2026-05-12", decision="thinking", destination_ids=[codes["IT"]])

    vera = await contact(client, "Вера", lead_channel_id=word["id"], partner_id=peter["id"])
    talk = await consult(client, vera, "2026-06-01", decision="yes", destination_ids=[codes["LV"]])
    latvia, _mother = await open_cases(
        client,
        codes,
        vera,
        "LV",
        consultation_id=talk["id"],
        applicants=[{"contact_id": vera["id"]}, {"first_name": "Мама", "label": "мать"}],
    )
    await patch(client, latvia, submission_on="2026-06-10")
    await patch(client, latvia, outcome="refusal", decided_on="2026-06-20")
    await price(client, latvia, "300", partner_terms="fixed", partner_fixed="50")

    gleb = await contact(client, "Глеб", lead_channel_id=word["id"])
    talk = await consult(client, gleb, "2026-04-28", decision="yes")
    [italy] = await open_cases(client, codes, gleb, "IT", consultation_id=talk["id"])
    await patch(client, italy, submission_on="2026-05-25")
    await patch(client, italy, outcome="not_submitted")

    # Poland's queue at the Minsk center: a letter after 126 days, one still waiting, one
    # taken off in June.
    [dasha] = await open_cases(
        client, codes, await contact(client, "Даша"), "PL", visa_center_id=minsk
    )
    [entry] = await ok(
        await client.post(f"/api/cases/{dasha['id']}/queue", json={"queued_on": "2026-01-09"})
    )
    await ok(
        await client.patch(
            f"/api/queue-entries/{entry['id']}",
            json={"letter_on": "2026-05-15", "appointment_on": "2026-06-15"},
        )
    )
    await patch(client, dasha, outcome="visa", decided_on="2026-06-25", visa_entries="single")
    await price(client, dasha, "400")
    [egor] = await open_cases(
        client, codes, await contact(client, "Егор"), "PL", visa_center_id=minsk
    )
    await ok(await client.post(f"/api/cases/{egor['id']}/queue", json={"queued_on": "2026-02-01"}))
    [fedor] = await open_cases(
        client, codes, await contact(client, "Фёдор"), "PL", visa_center_id=minsk
    )
    [entry] = await ok(
        await client.post(f"/api/cases/{fedor['id']}/queue", json={"queued_on": "2026-03-01"})
    )
    await ok(
        await client.patch(
            f"/api/queue-entries/{entry['id']}",
            json={"removed_on": "2026-06-05", "removed_reason": "передумал"},
        )
    )

    # France: the second application brought a date.
    [france] = await open_cases(client, codes, await contact(client, "Зоя"), "FR")
    first = await ok(
        await client.post(f"/api/cases/{france['id']}/attempts", json={"sent_on": "2026-03-01"})
    )
    await ok(await client.patch(f"/api/attempts/{first['id']}", json={"outcome": "expired"}))
    second = await ok(
        await client.post(f"/api/cases/{france['id']}/attempts", json={"sent_on": "2026-05-01"})
    )
    await ok(
        await client.patch(
            f"/api/attempts/{second['id']}",
            json={
                "outcome": "date_received",
                "closed_on": "2026-06-01",
                "appointment_on": "2026-07-01",
            },
        )
    )

    # When the cases were opened: Anna's and Vera's family in May, the rest earlier.
    async with owner_engine.begin() as conn:
        await conn.execute(text("UPDATE visa_cases SET created_at = '2026-01-05 10:00+00'"))
        await conn.execute(
            text("UPDATE visa_cases SET created_at = '2026-05-15 10:00+00' WHERE id = ANY(:ids)"),
            {"ids": [uuid.UUID(poland["id"]), uuid.UUID(latvia["id"]), uuid.UUID(_mother["id"])]},
        )
    return {
        "tenant_id": tenant_id,
        "codes": codes,
        "channels": (insta["id"], word["id"]),
        "peter": peter["id"],
        "minsk": minsk,
    }


async def test_the_reports_of_a_period(
    client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine
):
    made = await workspace(client, make_user, owner_engine)
    codes = made["codes"]
    insta, word = made["channels"]
    report = await ok(await client.get("/api/analytics", params=PERIOD))

    # Three consultations in May–June, two went to work; three cases opened for three
    # people, one family; three submissions (and one who did not go), two visas.
    assert report["totals"] == {
        "consultations": 3,
        "to_work": 2,
        "cases_opened": 3,
        "applicants": 3,
        "families": 1,
        "submissions": 3,
        "not_submitted": 1,
        "visas": 2,
        "refusals": 1,
        "approval": 67,
        "profit": "1150.00",  # 500 + (300 − 50) + 400
    }

    channels = {row["channel_id"]: row for row in report["channels"]}
    assert channels[insta] == {
        "channel_id": insta,
        "consultations": 2,
        "to_work": 1,
        "visas": 1,
        "profit": "500.00",
    }
    assert (channels[word]["consultations"], channels[word]["profit"]) == (1, "250.00")
    assert report["partners"] == [
        {
            "partner_id": made["peter"],
            "consultations": 1,
            "to_work": 1,
            "visas": 0,
            "profit": "250.00",
        }
    ]

    may, june = report["months"]
    assert (may["month"], may["consultations"], may["submissions"], may["profit"]) == (
        "2026-05-01",
        2,
        1,
        "500.00",
    )
    assert (june["to_work"], june["submissions"], june["visas"], june["profit"]) == (
        1,
        2,
        1,
        "650.00",
    )

    by_country = {row["country_id"]: row for row in report["countries"]}
    poland = by_country[codes["PL"]]
    assert (poland["submissions"], poland["visas"], poland["refusals"]) == (2, 2, 0)
    assert (poland["approval"], poland["multiple"], poland["review_days"]) == (100, 1, 12)
    assert by_country[codes["LV"]]["approval"] == 0
    assert by_country[codes["IT"]]["not_submitted"] == 1
    assert report["results"] == {"multiple_year": 1, "single": 1, "refusal": 1, "not_submitted": 1}

    # The Minsk center: both Polish visas, the wait for the letter, the specialist's note.
    noted = await client.put(
        f"/api/analytics/centers/{made['minsk']}/note", json={"note": "Сбои кабинета"}
    )
    assert noted.status_code == 204
    report = await ok(await client.get("/api/analytics", params=PERIOD))
    [minsk] = [row for row in report["centers"] if row["visa_center_id"] == made["minsk"]]
    assert minsk == {
        "visa_center_id": made["minsk"],
        "country_id": codes["PL"],
        "submissions": 2,
        "visas": 2,
        "refusals": 0,
        "approval": 100,
        "wait_days": 126,
        "attempts": None,
        "review_days": 12,
        "note": "Сбои кабинета",
    }
    [queue] = [q for q in report["queues"] if q["country_id"] == codes["PL"]]
    assert queue == {
        "country_id": codes["PL"],
        "visa_center_id": made["minsk"],
        "purpose_id": None,
        "waiting": 1,
        "letters": 1,
        "average_days": 126,
        "min_days": 126,
        "max_days": 126,
        "removed": 1,
    }
    [french] = [q for q in report["attempts"] if q["country_id"] == codes["FR"]]
    assert (french["dates"], french["average_attempts"]) == (1, 2.0)

    money = report["money"]
    assert (money["debt"], money["debtors"], money["partners_due"]) == ("700.00", 2, "50.00")

    # One country: Poland's consultations and cases only.
    polish = await ok(
        await client.get("/api/analytics", params=PERIOD | {"country_id": codes["PL"]})
    )
    totals = polish["totals"]
    assert (totals["consultations"], totals["submissions"], totals["visas"]) == (1, 2, 2)
    assert [row["country_id"] for row in polish["countries"]] == [codes["PL"]]

    # Clearing a note removes it; a bad period is refused.
    await ok(await client.put(f"/api/analytics/centers/{made['minsk']}/note", json={"note": " "}))
    report = await ok(await client.get("/api/analytics", params=PERIOD))
    assert [row["note"] for row in report["centers"] if row["visa_center_id"] == made["minsk"]] == [
        None
    ]
    backwards = await client.get(
        "/api/analytics", params={"start": "2026-06-30", "end": "2026-05-01"}
    )
    assert backwards.status_code == 422 and backwards.json()["detail"] == "invalid_period"
    unknown = await client.put(f"/api/analytics/centers/{uuid.uuid4()}/note", json={"note": "x"})
    assert unknown.status_code == 404


async def test_analytics_follows_modules_and_workspaces(
    client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine
):
    made = await workspace(client, make_user, owner_engine)
    await ok(
        await client.put(f"/api/analytics/centers/{made['minsk']}/note", json={"note": "Капча"})
    )

    client.cookies.clear()
    await make_user("olga@example.com", "employee", tenant_id=made["tenant_id"])

    async def give(*modules: str):
        async with owner_engine.begin() as conn:
            await conn.execute(
                text("UPDATE users SET modules = :m WHERE email = 'olga@example.com'"),
                {"m": list(modules)},
            )
        return await client.get("/api/analytics", params=PERIOD)

    await login(client, "olga@example.com")
    denied = await give("clients", "cases")
    assert denied.status_code == 403 and denied.json()["detail"] == "module_disabled"
    # Analytics without money: no profit anywhere.
    report = await ok(await give("clients", "cases", "analytics"))
    assert report["money"] is None and report["totals"]["profit"] is None
    assert {row["profit"] for row in report["channels"] + report["months"]} == {None}

    # Another workspace sees none of it, nor the note.
    client.cookies.clear()
    await make_user("boris@example.com")
    await login(client, "boris@example.com")
    other = await ok(await client.get("/api/analytics", params=PERIOD))
    assert other["totals"]["consultations"] == 0 and other["centers"] == []
    assert other["channels"] == [] and other["queues"] == []
