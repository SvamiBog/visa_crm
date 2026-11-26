"""Аналитика (backlog 1.13): reports of a period computed from the cards — channels,
months, countries and results, visa centers, queues, money and applicants. Profit of the
period and of its months is the money of their own days (backlog 1.18)."""

import uuid
from datetime import datetime

from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from tests.helpers import MakeUser, login

DICT = "/api/dictionaries"
MONEY = f"{DICT}/money"
PERIOD = {"start": "2026-05-01", "end": "2026-06-30"}
SPRING = {"start": "2026-03-01", "end": "2026-06-30"}


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


async def pay(client: AsyncClient, case: dict, paid_on: str, amount: str, **fields) -> None:
    body = {"paid_on": paid_on, "amount": amount, "currency": "BYN"} | fields
    await ok(await client.post(f"/api/cases/{case['id']}/payments", json=body))


async def spend(client: AsyncClient, case: dict, spent_on: str, amount: str) -> None:
    body = {"spent_on": spent_on, "amount": amount, "currency": "BYN", "description": "Курьер"}
    await ok(await client.post(f"/api/cases/{case['id']}/expenses", json=body))


async def finance(client: AsyncClient, case: dict, **fields) -> None:
    await ok(await client.patch(f"/api/cases/{case['id']}/finance", json=fields))


async def opened_at(owner_engine: AsyncEngine, case: dict, moment: str) -> None:
    async with owner_engine.begin() as conn:
        await conn.execute(
            text("UPDATE visa_cases SET created_at = :at WHERE id = :id"),
            {"at": datetime.fromisoformat(moment), "id": uuid.UUID(case["id"])},
        )


def profits(report: dict) -> list[str]:
    return [row["profit"] for row in report["months"]]


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
    await pay(client, poland, "2026-05-20", "500")

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
    # No price was named, but he paid something in June.
    await pay(client, italy, "2026-06-12", "80")

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
    # Her courier was paid in June; she has not paid yet.
    await spend(client, dasha, "2026-06-15", "120")
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
        "poland": poland,
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
        # The money of May and June: Anna's 500 and Gleb's 80 came in, 120 went to the
        # courier. The prices of the cases submitted (500 + 300 + 400) are not it.
        "profit": "460.00",
    }

    channels = {row["channel_id"]: row for row in report["channels"]}
    assert channels[insta] == {
        "channel_id": insta,
        "consultations": 2,
        "to_work": 1,
        "visas": 1,
        "profit": "500.00",
    }
    # A source keeps the whole profit of its cases: Vera's 300 less the partner's 50,
    # though she has paid nothing yet.
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
    # June is negative: 80 came in, 120 went out.
    assert (june["to_work"], june["submissions"], june["visas"], june["profit"]) == (
        1,
        2,
        1,
        "-40.00",
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
    # The money of the Polish cases only: Gleb's 80 was for Italy.
    assert (profits(polish), totals["profit"]) == (["500.00", "-120.00"], "380.00")

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

    # An employee with Anna's Polish case: its consultation, channel and money are theirs
    # to see.
    await make_user("olga@example.com", "employee", tenant_id=made["tenant_id"])
    [olga] = await ok(await client.get("/api/employees"))
    await ok(
        await client.put(
            f"/api/cases/{made['poland']['id']}/employees", json={"employee_ids": [olga["id"]]}
        )
    )
    client.cookies.clear()

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
    # Analytics without money: no profit anywhere — not of the period, nor of a month, nor
    # of the channel of the case.
    report = await ok(await give("clients", "cases", "analytics"))
    assert report["money"] is None and report["totals"]["profit"] is None
    assert len(report["channels"]) == 1 and profits(report) == [None, None]
    assert {row["profit"] for row in report["channels"]} == {None}
    # With «Финансы кейсов»: the money of the case given, 500 paid in May.
    report = await ok(await give("clients", "cases", "analytics", "finances"))
    assert (profits(report), report["totals"]["profit"]) == (["500.00", "0.00"], "500.00")
    assert [row["profit"] for row in report["channels"]] == ["500.00"]
    report = await ok(await give("clients", "cases", "analytics"))
    # The specialist's remark on a center is there to read, not to change (owner's answer,
    # audit h07.1).
    [minsk] = [row for row in report["centers"] if row["visa_center_id"] == made["minsk"]]
    assert minsk["note"] == "Капча"
    for note in ("Очередь с утра", ""):
        refused = await client.put(
            f"/api/analytics/centers/{made['minsk']}/note", json={"note": note}
        )
        assert refused.status_code == 403 and refused.json()["detail"] == "forbidden"

    # Another workspace sees none of it, nor the note.
    client.cookies.clear()
    await make_user("boris@example.com")
    await login(client, "boris@example.com")
    other = await ok(await client.get("/api/analytics", params=PERIOD))
    assert other["totals"]["consultations"] == 0 and other["centers"] == []
    assert other["channels"] == [] and other["queues"] == []
    assert (profits(other), other["totals"]["profit"]) == (["0.00", "0.00"], "0.00")


async def test_profit_is_the_money_of_each_month(
    client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine
):
    """Backlog 1.18: every amount counts in the month of its own day, whatever the price
    of the case and the day it was submitted."""
    await make_user("anna@example.com")
    await login(client, "anna@example.com")
    codes = await countries(client)
    await ok(await client.patch(MONEY, json={"tax_rate": "10"}))

    # Question 25, with the rest of the money of a case: a consultation held in February,
    # paid in March and credited, 200 in March, 750 officially and the talon in May (paid
    # before the CRM), a courier in April, the partner paid in June.
    anna = await contact(client, "Анна")
    talk = await consult(
        client,
        anna,
        "2026-02-25",
        fee_amount="50",
        fee_currency="BYN",
        fee_paid=True,
        fee_paid_on="2026-03-20",
    )
    [poland] = await open_cases(client, codes, anna, "PL", consultation_id=talk["id"])
    await finance(
        client,
        poland,
        price="1000",
        consultation_credit="50",
        registrar_fee_amount="150",
        registrar_fee_currency="BYN",
        registrar_fee_on="2026-05-12",
        registrar_fee_settled=True,
        partner_terms="fixed",
        partner_fixed="60",
        partner_paid_on="2026-06-01",
    )
    await pay(client, poland, "2026-03-05", "200")
    await pay(client, poland, "2026-05-12", "750", official=True)
    await spend(client, poland, "2026-04-02", "20")

    # No price yet: 100 EUR in June is 340 at the bank's rate; a percent of nothing is not
    # taken though the partner is marked paid.
    [latvia] = await open_cases(client, codes, await contact(client, "Вера"), "LV")
    await finance(
        client, latvia, partner_terms="percent", partner_percent="10", partner_paid_on="2026-06-02"
    )
    await pay(client, latvia, "2026-06-10", "100", currency="EUR")

    # A consultation not paid yet, its fee credited: the day it was held, April. A partner
    # not paid yet is in no month.
    boris = await contact(client, "Борис")
    talk = await consult(client, boris, "2026-04-18", fee_amount="30", fee_currency="BYN")
    [second] = await open_cases(client, codes, boris, "PL", consultation_id=talk["id"])
    await finance(
        client, second, consultation_credit="30", partner_terms="fixed", partner_fixed="5"
    )

    # A credit typed into a case without a consultation: the day the case was opened, in
    # her time zone — 22:30 UTC on 31 May is 1 June in Minsk.
    [third] = await open_cases(client, codes, await contact(client, "Глеб"), "PL")
    await finance(client, third, consultation_credit="40")
    await opened_at(owner_engine, third, "2026-05-31T22:30:00+00:00")

    # A paid consultation no case came from is not profit (the owner, 2026-10-28).
    await consult(
        client,
        await contact(client, "Даша"),
        "2026-04-10",
        fee_amount="70",
        fee_currency="BYN",
        fee_paid=True,
        fee_paid_on="2026-04-10",
    )

    # March 200 + 50; April 30 − 20; May 750 − 75 of tax − 150; June 340 + 40 − 60.
    report = await ok(await client.get("/api/analytics", params=SPRING))
    assert [row["month"] for row in report["months"]] == [
        "2026-03-01",
        "2026-04-01",
        "2026-05-01",
        "2026-06-01",
    ]
    assert profits(report) == ["250.00", "10.00", "525.00", "320.00"]
    assert report["totals"]["profit"] == "1105.00"  # the sum of its months

    # A period takes the money of its own days only.
    may = await ok(
        await client.get("/api/analytics", params={"start": "2026-05-12", "end": "2026-05-31"})
    )
    assert (profits(may), may["totals"]["profit"]) == (["525.00"], "525.00")
    before = await ok(
        await client.get("/api/analytics", params={"start": "2026-05-01", "end": "2026-05-11"})
    )
    assert (profits(before), before["totals"]["profit"]) == (["0.00"], "0.00")

    # One country: the money of the cases whose visa is for it.
    polish = await ok(
        await client.get("/api/analytics", params=SPRING | {"country_id": codes["PL"]})
    )
    assert profits(polish) == ["250.00", "10.00", "525.00", "-20.00"]
    assert polish["totals"]["profit"] == "765.00"
    latvian = await ok(
        await client.get("/api/analytics", params=SPRING | {"country_id": codes["LV"]})
    )
    assert profits(latvian) == ["0.00", "0.00", "0.00", "340.00"]

    # No tax while she has not set her rate.
    await ok(await client.patch(MONEY, json={"tax_rate": None}))
    report = await ok(await client.get("/api/analytics", params=SPRING))
    assert profits(report) == ["250.00", "10.00", "600.00", "320.00"]


async def test_an_employee_sees_the_profit_of_their_cases_only(
    client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine
):
    tenant_id = await make_user("anna@example.com")
    await login(client, "anna@example.com")
    codes = await countries(client)
    [given] = await open_cases(client, codes, await contact(client, "Анна"), "PL")
    await pay(client, given, "2026-05-20", "300")
    await finance(
        client,
        given,
        registrar_fee_amount="100",
        registrar_fee_currency="BYN",
        registrar_fee_on="2026-06-02",
    )
    [hidden] = await open_cases(client, codes, await contact(client, "Борис"), "PL")
    await pay(client, hidden, "2026-05-21", "500")
    await spend(client, hidden, "2026-06-03", "40")
    await finance(client, hidden, consultation_credit="25")
    await opened_at(owner_engine, hidden, "2026-05-15T10:00:00+00:00")

    await make_user("olga@example.com", "employee", tenant_id=tenant_id)
    async with owner_engine.begin() as conn:
        await conn.execute(
            text("UPDATE users SET modules = :m WHERE email = 'olga@example.com'"),
            {"m": ["clients", "cases", "analytics", "finances"]},
        )
    [olga] = await ok(await client.get("/api/employees"))
    await ok(
        await client.put(f"/api/cases/{given['id']}/employees", json={"employee_ids": [olga["id"]]})
    )
    whole = await ok(await client.get("/api/analytics", params=PERIOD))
    assert (profits(whole), whole["totals"]["profit"]) == (["825.00", "-140.00"], "685.00")

    client.cookies.clear()
    await login(client, "olga@example.com")
    report = await ok(await client.get("/api/analytics", params=PERIOD))
    assert (profits(report), report["totals"]["profit"]) == (["300.00", "-100.00"], "200.00")


async def test_a_credit_whose_consultation_an_employee_may_not_see_counts_when_the_case_opened(
    client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine
):
    """The mother consulted and paid in March, the case is for her son alone, opened in
    June with the fee credited. The specialist sees the credit in March. An employee given
    the son's case does not see the mother's card nor her consultation (`employee_scope`),
    so for them the credit is one of a case without a consultation: June. The amount is the
    same, nothing of the hidden card is shown."""
    tenant_id = await make_user("anna@example.com")
    await login(client, "anna@example.com")
    codes = await countries(client)
    mother = await contact(client, "Мама")
    talk = await consult(
        client,
        mother,
        "2026-03-18",
        fee_amount="50",
        fee_currency="BYN",
        fee_paid=True,
        fee_paid_on="2026-03-20",
    )
    [son] = await open_cases(
        client,
        codes,
        mother,
        "PL",
        consultation_id=talk["id"],
        applicants=[{"first_name": "Сын", "label": "сын"}],
    )
    await finance(client, son, consultation_credit="50")
    await opened_at(owner_engine, son, "2026-06-03T10:00:00+00:00")
    whole = await ok(await client.get("/api/analytics", params=SPRING))
    assert profits(whole) == ["50.00", "0.00", "0.00", "0.00"]

    await make_user("olga@example.com", "employee", tenant_id=tenant_id)
    async with owner_engine.begin() as conn:
        await conn.execute(
            text("UPDATE users SET modules = :m WHERE email = 'olga@example.com'"),
            {"m": ["clients", "cases", "analytics", "finances"]},
        )
    [olga] = await ok(await client.get("/api/employees"))
    await ok(
        await client.put(f"/api/cases/{son['id']}/employees", json={"employee_ids": [olga["id"]]})
    )
    client.cookies.clear()
    await login(client, "olga@example.com")
    report = await ok(await client.get("/api/analytics", params=SPRING))
    assert profits(report) == ["0.00", "0.00", "0.00", "50.00"]
    assert report["totals"]["consultations"] == 0  # the mother's consultation is not theirs


async def test_a_case_priced_in_another_currency_counts_in_the_base_one(
    client: AsyncClient, make_user: MakeUser
):
    # Pilot fix 4: 500 PLN at 0.80 is 400 BYN; the partner's 10% is 40 BYN, and so is the
    # credit of 50 PLN.
    await make_user("anna@example.com")
    await login(client, "anna@example.com")
    codes = await countries(client)
    insta = await ok(await client.post(f"{DICT}/channels", json={"name": "Instagram"}))
    anna = await contact(client, "Анна", lead_channel_id=insta["id"])
    talk = await consult(client, anna, "2026-05-10", decision="yes")
    [case] = await open_cases(client, codes, anna, "PL", consultation_id=talk["id"])
    await finance(
        client,
        case,
        price="500",
        currency="PLN",
        consultation_credit="50",
        partner_terms="percent",
        partner_percent="10",
        partner_paid_on="2026-06-03",
    )
    report = await ok(await client.get("/api/analytics", params=PERIOD))
    # The channel: the profit of the case, its price less the partner's payout.
    assert [row["profit"] for row in report["channels"]] == ["360.00"]
    # The months: the credit on the day of the consultation, the payout on the day it was paid.
    assert (profits(report), report["totals"]["profit"]) == (["40.00", "-40.00"], "0.00")
