"""Countries and operators are shared (the admin keeps them); directions — «destination ×
submission country» rules with their cities — belong to each workspace (backlog 1.20)."""

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.db import scope_to_tenant
from app.models import Country, CountryRule, VisaCenter
from tests.helpers import MakeUser, login

RULES = "/api/reference/rules"


async def as_admin(client: AsyncClient, make_user: MakeUser) -> None:
    await make_user("root@example.com", role="admin")
    assert (await login(client, "root@example.com")).status_code == 200


async def as_specialist(
    client: AsyncClient, make_user: MakeUser, email: str = "anna@example.com", **options
):
    tenant_id = await make_user(email, **options)
    await login(client, email)
    return tenant_id


async def country_id(client: AsyncClient, code: str) -> str:
    """Works for the admin (admin panel list) and workspace users (workspace list)."""
    response = await client.get("/api/reference/countries")
    if response.status_code == 403:
        response = await client.get("/api/admin/reference/countries")
    return next(c["id"] for c in response.json() if c["code"] == code)


async def codes(client: AsyncClient) -> dict[str, str]:
    return {c["code"]: c["id"] for c in (await client.get("/api/reference/countries")).json()}


def by_destination(rules: list[dict], ids: dict[str, str]) -> dict[str, dict]:
    code_of = {v: k for k, v in ids.items()}
    return {code_of[r["destination_id"]]: r for r in rules}


async def add_direction(client: AsyncClient, destinations: list[str], **fields) -> list[dict]:
    ids = await codes(client)
    body = {
        "destination_ids": [ids[d] for d in destinations],
        "submission_id": ids["RS"],
        "booking_type": "direct_slot",
    } | fields
    response = await client.post(RULES, json=body)
    assert response.status_code == 201, response.text
    return response.json()


async def test_new_workspace_starts_without_directions(client: AsyncClient, make_user: MakeUser):
    await as_specialist(client, make_user, directions=False)
    assert (await client.get(RULES)).json() == []


async def test_specialist_sets_up_one_direction_for_several_countries(
    client: AsyncClient, make_user: MakeUser
):
    """Belgium, Luxembourg, Slovenia: the same short application at the German embassy."""
    await as_specialist(client, make_user, directions=False)
    ids = await codes(client)
    response = await client.post(
        RULES,
        json={
            "destination_ids": [ids["BE"], ids["LU"], ids["SI"]],
            "submission_id": ids["BY"],
            "booking_type": "waiting_list",
            "represented_by_id": ids["DE"],
            "identifier_type": "application_number",
            "notes": "Через посольство Германии",
            "cities": ["Минск"],
        },
    )
    assert response.status_code == 201, response.text
    created = by_destination(response.json(), ids)
    assert set(created) == {"BE", "LU", "SI"}
    for rule in created.values():
        assert rule["booking_type"] == "waiting_list" and rule["represented_by_id"] == ids["DE"]
        assert [c["city_ru"] for c in rule["visa_centers"]] == ["Минск"]
        # One field for the city: the English name is optional.
        assert rule["visa_centers"][0]["city_en"] is None

    listed = by_destination((await client.get(RULES, params={"submission": ids["BY"]})).json(), ids)
    assert set(listed) == {"BE", "LU", "SI"}

    # All or nothing: one country already has a direction, none is added.
    again = await client.post(
        RULES,
        json={
            "destination_ids": [ids["NL"], ids["BE"]],
            "submission_id": ids["BY"],
            "booking_type": "waiting_list",
        },
    )
    assert again.status_code == 409 and again.json()["detail"] == "rule_exists"
    assert "NL" not in by_destination((await client.get(RULES)).json(), ids)


async def test_cities_are_added_once(client: AsyncClient, make_user: MakeUser):
    await as_specialist(client, make_user, directions=False)
    [rule] = await add_direction(client, ["JP"], cities=["Белград"])

    response = await client.post(
        f"{RULES}/{rule['id']}/visa-centers", json={"cities": ["белград", " Ниш ", "Ниш"]}
    )
    assert response.status_code == 200, response.text
    assert [c["city_ru"] for c in response.json()["visa_centers"]] == ["Белград", "Ниш"]

    nis = next(c for c in response.json()["visa_centers"] if c["city_ru"] == "Ниш")
    renamed = await client.patch(
        f"/api/reference/visa-centers/{nis['id']}", json={"city_ru": "Белград"}
    )
    assert renamed.status_code == 409 and renamed.json()["detail"] == "visa_center_exists"
    named = await client.patch(f"/api/reference/visa-centers/{nis['id']}", json={"city_en": "Nis"})
    assert named.status_code == 200 and named.json()["city_en"] == "Nis"


async def test_windowed_application_needs_window_days(client: AsyncClient, make_user: MakeUser):
    await as_specialist(client, make_user, directions=False)
    ids = await codes(client)
    body = {
        "destination_ids": [ids["JP"]],
        "submission_id": ids["RS"],
        "booking_type": "windowed_application",
    }
    response = await client.post(RULES, json=body)
    assert response.status_code == 422
    assert response.json()["detail"] == "window_days_required"

    [rule] = (await client.post(RULES, json={**body, "window_days": 60})).json()
    # Another booking type has no window: the old value is cleared, not left behind.
    changed = await client.patch(f"{RULES}/{rule['id']}", json={"booking_type": "waiting_list"})
    assert changed.status_code == 200
    assert changed.json()["window_days"] is None

    back = await client.patch(
        f"{RULES}/{rule['id']}", json={"booking_type": "windowed_application"}
    )
    assert back.status_code == 422


async def test_appeal_deadline_only_when_appeals_accepted(client: AsyncClient, make_user: MakeUser):
    await as_specialist(client, make_user, directions=False)
    [rule] = await add_direction(client, ["JP"], appeals_accepted=True, appeal_deadline_days=15)
    assert rule["appeal_deadline_days"] == 15
    changed = await client.patch(f"{RULES}/{rule['id']}", json={"appeals_accepted": False})
    assert changed.json()["appeal_deadline_days"] is None


async def test_direction_cannot_point_a_country_at_itself(client: AsyncClient, make_user: MakeUser):
    await as_specialist(client, make_user, directions=False)
    belarus = await country_id(client, "BY")
    response = await client.post(
        RULES,
        json={"destination_ids": [belarus], "submission_id": belarus, "booking_type": "embassy"},
    )
    assert response.status_code == 422


async def test_unknown_country_or_operator_is_not_found(client: AsyncClient, make_user: MakeUser):
    await as_specialist(client, make_user, directions=False)
    missing = "00000000-0000-0000-0000-000000000000"
    [rule] = await add_direction(client, ["JP"])
    response = await client.patch(f"{RULES}/{rule['id']}", json={"vac_operator_id": missing})
    assert response.status_code == 404
    created = await client.post(
        RULES,
        json={
            "destination_ids": [missing],
            "submission_id": await country_id(client, "BY"),
            "booking_type": "embassy",
        },
    )
    assert created.status_code == 404


async def test_unused_direction_and_city_are_deleted(client: AsyncClient, make_user: MakeUser):
    await as_specialist(client, make_user, directions=False)
    [rule] = await add_direction(client, ["JP"], cities=["Белград", "Ниш"])
    nis = next(c for c in rule["visa_centers"] if c["city_ru"] == "Ниш")

    assert (await client.delete(f"/api/reference/visa-centers/{nis['id']}")).status_code == 204
    [left] = (await client.get(RULES)).json()
    assert [c["city_ru"] for c in left["visa_centers"]] == ["Белград"]

    assert (await client.delete(f"{RULES}/{rule['id']}")).status_code == 204
    assert (await client.get(RULES)).json() == []

    events = (await client.get("/api/audit")).json()["events"]
    assert [(e["action"], e["target_type"]) for e in events[:2]] == [
        ("reference.deleted", "country_rule"),
        ("reference.deleted", "visa_center"),
    ]


async def test_switched_off_entries_stay_for_the_builder_only(
    client: AsyncClient, make_user: MakeUser
):
    await as_specialist(client, make_user, directions=False)
    [japan, usa] = await add_direction(client, ["JP", "US"], cities=["Белград", "Ниш"])
    nis = next(c for c in japan["visa_centers"] if c["city_ru"] == "Ниш")
    await client.patch(f"/api/reference/visa-centers/{nis['id']}", json={"is_active": False})
    await client.patch(f"{RULES}/{usa['id']}", json={"is_active": False})

    # Forms see what is on; the builder also sees what is switched off.
    [shown] = (await client.get(RULES)).json()
    assert shown["id"] == japan["id"]
    assert [c["city_ru"] for c in shown["visa_centers"]] == ["Белград"]
    everything = (await client.get(RULES, params={"include_inactive": True})).json()
    assert {r["id"] for r in everything} == {japan["id"], usa["id"]}
    [built] = [r for r in everything if r["id"] == japan["id"]]
    assert {c["city_ru"]: c["is_active"] for c in built["visa_centers"]} == {
        "Белград": True,
        "Ниш": False,
    }


async def test_directions_are_in_the_workspace_log(client: AsyncClient, make_user: MakeUser):
    await as_specialist(client, make_user, directions=False)
    [rule] = await add_direction(client, ["JP"])
    await client.patch(f"{RULES}/{rule['id']}", json={"notes": "Запись по звонку"})
    await client.post(f"{RULES}/{rule['id']}/visa-centers", json={"cities": ["Ниш"]})
    events = (await client.get("/api/audit")).json()["events"]
    assert [(e["action"], e["target_type"]) for e in events[:3]] == [
        ("reference.created", "visa_center"),
        ("reference.updated", "country_rule"),
        ("reference.created", "country_rule"),
    ]


async def test_employee_reads_directions_but_does_not_edit_them(
    client: AsyncClient, make_user: MakeUser
):
    tenant_id = await as_specialist(client, make_user, directions=False)
    [rule] = await add_direction(client, ["JP"], cities=["Белград"])
    client.cookies.clear()
    await make_user("helper@example.com", "employee", tenant_id=tenant_id)
    await login(client, "helper@example.com")

    assert [r["id"] for r in (await client.get(RULES)).json()] == [rule["id"]]
    ids = await codes(client)
    attempts = [
        client.post(
            RULES,
            json={
                "destination_ids": [ids["US"]],
                "submission_id": ids["RS"],
                "booking_type": "embassy",
            },
        ),
        client.patch(f"{RULES}/{rule['id']}", json={"notes": "x"}),
        client.post(f"{RULES}/{rule['id']}/visa-centers", json={"cities": ["Ниш"]}),
        client.delete(f"{RULES}/{rule['id']}"),
    ]
    for attempt in attempts:
        response = await attempt
        assert response.status_code == 403 and response.json()["detail"] == "forbidden"


async def test_other_workspaces_directions_are_not_seen(
    client: AsyncClient,
    make_user: MakeUser,
    sessionmaker: async_sessionmaker[AsyncSession],
):
    anna = await as_specialist(client, make_user, directions=False)
    [rule] = await add_direction(client, ["JP"], cities=["Белград"])
    center = rule["visa_centers"][0]["id"]
    client.cookies.clear()
    boris = await as_specialist(client, make_user, "boris@example.com", directions=False)

    assert (await client.get(RULES, params={"include_inactive": True})).json() == []
    for response in [
        await client.patch(f"{RULES}/{rule['id']}", json={"notes": "x"}),
        await client.post(f"{RULES}/{rule['id']}/visa-centers", json={"cities": ["Ниш"]}),
        await client.patch(f"/api/reference/visa-centers/{center}", json={"is_active": False}),
        await client.delete(f"/api/reference/visa-centers/{center}"),
        await client.delete(f"{RULES}/{rule['id']}"),
    ]:
        assert response.status_code == 404
    # The same pair is free in another workspace: directions are per workspace.
    assert len(await add_direction(client, ["JP"])) == 1

    async with sessionmaker() as session:
        scope_to_tenant(session, boris)
        assert await session.scalar(select(func.count()).select_from(VisaCenter)) == 0
    async with sessionmaker() as session:
        scope_to_tenant(session, anna)
        assert await session.scalar(select(func.count()).select_from(CountryRule)) == 1


async def test_admin_keeps_countries_and_operators_only(client: AsyncClient, make_user: MakeUser):
    await as_admin(client, make_user)
    assert (await client.get("/api/admin/reference/countries")).status_code == 200
    assert (await client.get("/api/admin/reference/operators")).status_code == 200
    assert (await client.get("/api/admin/reference/rules")).status_code == 404


async def test_admin_changes_are_audited(client: AsyncClient, make_user: MakeUser):
    await as_admin(client, make_user)
    created = await client.post(
        "/api/admin/reference/operators", json={"code": "visa_test", "name": "Visa Test"}
    )
    assert created.status_code == 201
    await client.patch(
        f"/api/admin/reference/operators/{created.json()['id']}", json={"is_active": False}
    )
    events = (await client.get("/api/admin/audit")).json()["events"]
    actions = [(e["action"], e["target_type"]) for e in events[:2]]
    assert actions == [
        ("reference.updated", "vac_operator"),
        ("reference.created", "vac_operator"),
    ]


async def test_switched_off_country_hides_its_directions(client: AsyncClient, make_user: MakeUser):
    await as_admin(client, make_user)
    japan = await country_id(client, "JP")
    await client.patch(f"/api/admin/reference/countries/{japan}", json={"is_active": False})
    admin_codes = [c["code"] for c in (await client.get("/api/admin/reference/countries")).json()]
    assert "JP" in admin_codes

    poland = await country_id(client, "PL")
    await client.patch(f"/api/admin/reference/countries/{poland}", json={"is_active": False})

    client.cookies.clear()
    await as_specialist(client, make_user)
    country_codes = [c["code"] for c in (await client.get("/api/reference/countries")).json()]
    assert "JP" not in country_codes and "PL" not in country_codes and "FR" in country_codes
    # The test workspace has the standard directions from Belarus (tests/conftest.py).
    belarus = await country_id(client, "BY")
    rules = (await client.get(RULES, params={"submission": belarus})).json()
    assert poland not in {r["destination_id"] for r in rules}
    assert len(rules) == 27


@pytest.mark.parametrize("role", ["specialist", "employee"])
async def test_only_admin_edits_shared_reference(
    client: AsyncClient, make_user: MakeUser, role: str
):
    await make_user("someone@example.com", role)
    await login(client, "someone@example.com")
    response = await client.post(
        "/api/admin/reference/countries",
        json={"code": "XA", "name_ru": "Тест", "name_en": "Test"},
    )
    assert response.status_code == 403
    # Reading is open to every workspace user: forms need the country list.
    assert (await client.get("/api/reference/countries")).status_code == 200


async def test_reference_needs_login(client: AsyncClient):
    assert (await client.get("/api/reference/countries")).status_code == 401
    assert (await client.get(RULES)).status_code == 401


async def test_workspace_scope_cannot_write_shared_reference(
    sessionmaker: async_sessionmaker[AsyncSession], make_user: MakeUser
):
    """Row-Level Security: a workspace reads the shared reference but never changes it."""
    tenant_id = await make_user("anna@example.com")
    async with sessionmaker() as session:
        scope_to_tenant(session, tenant_id)
        assert await session.scalar(select(Country.code).where(Country.code == "PL")) == "PL"
        # Rows outside the write policy are skipped, not changed.
        updated = await session.execute(text("UPDATE countries SET name_en = 'X'"))
        assert updated.rowcount == 0
        deleted = await session.execute(text("DELETE FROM vac_operators"))
        assert deleted.rowcount == 0
        with pytest.raises(DBAPIError):
            await session.execute(
                text(
                    "INSERT INTO countries (id, code, name_ru, name_en) "
                    "VALUES (gen_random_uuid(), 'XA', 'Тест', 'Test')"
                )
            )


async def test_no_scope_sees_no_reference(
    sessionmaker: async_sessionmaker[AsyncSession], make_user: MakeUser
):
    await make_user("anna@example.com")
    async with sessionmaker() as session:
        assert (await session.scalars(select(Country.code))).all() == []
        assert (await session.scalars(select(CountryRule.id))).all() == []
