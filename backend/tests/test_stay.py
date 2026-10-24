"""The 90/180 calculator: trips in the client's card and the count (backlog 2.6)."""

import uuid
from datetime import timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from tests.helpers import MakeUser, login, workspace_today

TODAY = workspace_today()


def day(offset: int) -> str:
    return (TODAY + timedelta(days=offset)).isoformat()


async def turn_on(owner_engine: AsyncEngine, tenant_id: uuid.UUID | None) -> None:
    """Stage 2 modules are not in a new workspace by default: the admin turns them on."""
    async with owner_engine.begin() as conn:
        await conn.execute(
            text(
                "UPDATE tenants SET modules = array_append(modules, 'stay_calculator') "
                "WHERE id = :t"
            ),
            {"t": tenant_id},
        )


async def as_specialist(
    client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine, email="anna@example.com"
) -> uuid.UUID | None:
    tenant_id = await make_user(email)
    await turn_on(owner_engine, tenant_id)
    await login(client, email)
    return tenant_id


async def countries(client: AsyncClient) -> dict[str, str]:
    return {c["code"]: c["id"] for c in (await client.get("/api/reference/countries")).json()}


async def contact(client: AsyncClient, name: str = "Анна") -> str:
    return (await client.post("/api/contacts", json={"first_name": name})).json()["id"]


async def add_trip(client: AsyncClient, contact_id: str, country: str, **body) -> dict:
    response = await client.post(
        f"/api/contacts/{contact_id}/trips",
        json={"country_id": (await countries(client))[country], **body},
    )
    assert response.status_code == 201, response.text
    return response.json()


async def trips(client: AsyncClient, contact_id: str) -> dict:
    response = await client.get(f"/api/contacts/{contact_id}/trips")
    assert response.status_code == 200, response.text
    return response.json()


async def test_trips_in_the_card_are_counted(
    client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine
):
    await as_specialist(client, make_user, owner_engine)
    anna = await contact(client)
    earlier = await add_trip(client, anna, "PL", entered_on=day(-100), left_on=day(-91))
    later = await add_trip(
        client, anna, "IT", entered_on=day(-30), left_on=day(-21), note=" по визе Италии "
    )
    assert later["note"] == "по визе Италии"
    # Outside the area and under a long-stay visa: kept, but not counted.
    await add_trip(client, anna, "GB", entered_on=day(-60), left_on=day(-50))
    await add_trip(client, anna, "PL", entered_on=day(-80), left_on=day(-70), long_stay=True)

    card = await trips(client, anna)
    # Latest first, and the count for each trip in the same order.
    assert [t["entered_on"] for t in card["trips"]] == [day(-30), day(-60), day(-80), day(-100)]
    results = card["stay"]["trips"]
    assert [(r["counts"], r["days"]) for r in results] == [
        (True, 10),
        (False, 0),
        (False, 0),
        (True, 10),
    ]
    assert results[0]["used_at_end"] == 20
    assert card["trips"][3]["id"] == earlier["id"]

    stay = card["stay"]
    assert (stay["today"], stay["max_days"], stay["window_days"]) == (day(0), 90, 180)
    assert stay["summary"]["used"] == 20
    assert stay["summary"]["in_area"] is False
    assert stay["summary"]["overstays"] == []
    # 70 days left while both trips are in the period; the earlier one leaves it on day 80.
    assert stay["summary"]["stay_until"] == day(69)


async def test_a_trip_still_going_on_counts_up_to_today(
    client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine
):
    await as_specialist(client, make_user, owner_engine)
    anna = await contact(client)
    trip = await add_trip(client, anna, "DE", entered_on=day(-9))
    assert trip["left_on"] is None
    summary = (await trips(client, anna))["stay"]["summary"]
    assert (summary["used"], summary["in_area"]) == (10, True)
    assert summary["stay_until"] == day(80)

    # She came back yesterday.
    updated = await client.patch(f"/api/trips/{trip['id']}", json={"left_on": day(-1)})
    assert updated.status_code == 200 and updated.json()["left_on"] == day(-1)
    summary = (await trips(client, anna))["stay"]["summary"]
    assert (summary["used"], summary["in_area"]) == (9, False)


async def test_overstays_are_shown(
    client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine
):
    await as_specialist(client, make_user, owner_engine)
    anna = await contact(client)
    await add_trip(client, anna, "FR", entered_on=day(-150), left_on=day(-51))  # 100 days
    stay = (await trips(client, anna))["stay"]
    assert stay["summary"]["overstays"] == [{"first": day(-60), "last": day(-51)}]
    assert stay["trips"][0]["overstay_from"] == day(-60)
    assert stay["trips"][0]["used_at_end"] == 100


async def test_a_country_counts_from_the_day_it_joined(
    client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine
):
    await make_user("root@example.com", role="admin")
    await login(client, "root@example.com")
    admin_countries = (await client.get("/api/admin/reference/countries")).json()
    by_code = {c["code"]: c for c in admin_countries}
    assert by_code["BG"]["schengen_since"] == "2024-03-31"
    assert by_code["CY"]["schengen_since"] is None
    assert by_code["IE"]["schengen_since"] is None
    # Members missing from the first seed are added.
    assert {"IS", "LI", "NO", "CH"} <= set(by_code)
    assert by_code["CH"]["schengen_since"] == "2008-12-12"
    # The day Cyprus joins, the admin enters it.
    joined = await client.patch(
        f"/api/admin/reference/countries/{by_code['CY']['id']}", json={"schengen_since": day(-5)}
    )
    assert joined.status_code == 200 and joined.json()["schengen_since"] == day(-5)

    client.cookies.clear()
    await as_specialist(client, make_user, owner_engine)
    anna = await contact(client)
    await add_trip(client, anna, "CY", entered_on=day(-20), left_on=day(-1))
    assert (await trips(client, anna))["stay"]["trips"][0]["days"] == 5
    # A trip from before it joined counts nothing.
    await add_trip(client, anna, "CY", entered_on=day(-40), left_on=day(-30))
    earlier = (await trips(client, anna))["stay"]["trips"][1]
    assert (earlier["counts"], earlier["days"]) == (False, 0)


async def test_the_calculator_counts_trips_that_are_not_saved(
    client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine
):
    await as_specialist(client, make_user, owner_engine)
    codes = await countries(client)
    body = {
        "trips": [
            {"country_id": codes["PL"], "entered_on": day(-60), "left_on": day(-1)},
            {"country_id": codes["TR"], "entered_on": day(-90), "left_on": day(-80)},
        ],
        "check": {"entered_on": day(10), "left_on": day(50)},
    }
    response = await client.post("/api/stay/calculate", json=body)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["summary"]["used"] == 60
    assert [t["counts"] for t in result["trips"]] == [True, False]
    check = result["check"]
    assert (check["length"], check["used_before"], check["fits"]) == (41, 60, False)
    assert check["stay_until"] == day(39)
    assert check["earliest_entry"] is not None
    # Nothing is stored: no trips, no audit events.
    events = (await client.get("/api/audit")).json()["events"]
    assert not [e for e in events if e["action"].startswith("contacts.trip")]

    # Without an exit date the answer is how long the planned trip may last.
    open_check = await client.post(
        "/api/stay/calculate", json={**body, "check": {"entered_on": day(10)}}
    )
    assert open_check.status_code == 200, open_check.text
    assert open_check.json()["check"]["fits"] is None
    assert open_check.json()["check"]["stay_until"] == day(39)

    empty = await client.post("/api/stay/calculate", json={})
    assert empty.status_code == 200
    assert empty.json()["summary"]["stay_until"] == day(89)


@pytest.mark.parametrize(
    ("trip", "code"),
    [
        ({"entered_on": day(-5), "left_on": day(-6)}, "dates_order"),
        ({"entered_on": day(5)}, "trip_exit_required"),
        ({"entered_on": day(-400), "left_on": day(-10)}, "trip_too_long"),
        ({"entered_on": day(800), "left_on": day(810)}, "trip_too_far"),
        ({"entered_on": "1980-01-01", "left_on": "1980-01-05"}, "validation"),
    ],
)
async def test_trip_dates_are_checked(
    client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine, trip: dict, code: str
):
    await as_specialist(client, make_user, owner_engine)
    poland = (await countries(client))["PL"]
    anna = await contact(client)
    saved = await client.post(f"/api/contacts/{anna}/trips", json={"country_id": poland, **trip})
    counted = await client.post(
        "/api/stay/calculate", json={"trips": [{"country_id": poland, **trip}]}
    )
    for response in (saved, counted):
        assert response.status_code == 422
        if code != "validation":
            assert response.json()["detail"] == code


async def test_long_stays_may_be_long_and_countries_must_exist(
    client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine
):
    await as_specialist(client, make_user, owner_engine)
    anna = await contact(client)
    # Two years under a residence permit is fine: the days just do not count.
    await add_trip(client, anna, "PL", entered_on=day(-800), left_on=day(-70), long_stay=True)
    unknown = await client.post(
        f"/api/contacts/{anna}/trips", json={"country_id": str(uuid.uuid4()), "entered_on": day(-3)}
    )
    assert unknown.status_code == 422 and unknown.json()["detail"] == "unknown_country"


async def test_trips_go_to_the_audit_log_by_id_only(
    client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine
):
    await as_specialist(client, make_user, owner_engine)
    anna = await contact(client)
    trip = await add_trip(client, anna, "ES", entered_on=day(-10), left_on=day(-3))
    await client.patch(f"/api/trips/{trip['id']}", json={"note": "отпуск", "left_on": day(-2)})
    assert (await client.delete(f"/api/trips/{trip['id']}")).status_code == 204
    assert (await trips(client, anna))["trips"] == []

    events = [
        e
        for e in (await client.get("/api/audit")).json()["events"]
        if e["action"].startswith("contacts.trip")
    ]
    assert [e["action"] for e in events] == [
        "contacts.trip_deleted",
        "contacts.trip_updated",
        "contacts.trip_added",
    ]
    for event in events:
        assert event["target_id"] == anna
        assert event["details"]["trip"] == trip["id"]
    assert events[1]["details"]["fields"] == "left_on,note"


async def test_calculator_needs_its_module_and_trips_stay_in_the_workspace(
    client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine
):
    tenant_id = await as_specialist(client, make_user, owner_engine)
    anna = await contact(client)
    trip = await add_trip(client, anna, "PL", entered_on=day(-10), left_on=day(-3))

    # Another workspace sees nothing.
    client.cookies.clear()
    await as_specialist(client, make_user, owner_engine, "boris@example.com")
    assert (await client.get(f"/api/contacts/{anna}/trips")).status_code == 404
    assert (await client.patch(f"/api/trips/{trip['id']}", json={"note": "x"})).status_code == 404
    assert (await client.delete(f"/api/trips/{trip['id']}")).status_code == 404

    # A workspace without the module (the default for a new one).
    client.cookies.clear()
    await make_user("vera@example.com")
    await login(client, "vera@example.com")
    denied = await client.post("/api/stay/calculate", json={})
    assert denied.status_code == 403 and denied.json()["detail"] == "module_disabled"

    # An employee needs the calculator and the clients for the card's trips.
    client.cookies.clear()
    await make_user("olga@example.com", "employee", tenant_id=tenant_id)
    async with owner_engine.begin() as conn:
        await conn.execute(
            text("UPDATE users SET modules = '{stay_calculator}' WHERE email = 'olga@example.com'")
        )
    await login(client, "olga@example.com")
    assert (await client.post("/api/stay/calculate", json={})).status_code == 200
    denied = await client.get(f"/api/contacts/{anna}/trips")
    assert denied.status_code == 403 and denied.json()["detail"] == "module_disabled"
