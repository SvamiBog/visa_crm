import pytest
from httpx import AsyncClient
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.db import scope_to_tenant
from app.models import Country
from tests.helpers import MakeUser, login


async def as_admin(client: AsyncClient, make_user: MakeUser) -> None:
    await make_user("root@example.com", role="admin")
    assert (await login(client, "root@example.com")).status_code == 200


async def country_id(client: AsyncClient, code: str) -> str:
    """Works for the admin (admin panel list) and workspace users (workspace list)."""
    response = await client.get("/api/reference/countries")
    if response.status_code == 403:
        response = await client.get("/api/admin/reference/countries")
    return next(c["id"] for c in response.json() if c["code"] == code)


def by_destination(rules: list[dict], countries: list[dict]) -> dict[str, dict]:
    codes = {c["id"]: c["code"] for c in countries}
    return {codes[r["destination_id"]]: r for r in rules}


async def test_belarus_rules_are_seeded(client: AsyncClient, make_user: MakeUser):
    await make_user("anna@example.com")
    await login(client, "anna@example.com")
    countries = (await client.get("/api/reference/countries")).json()
    operators = {o["id"]: o["code"] for o in (await client.get("/api/reference/operators")).json()}
    belarus = await country_id(client, "BY")

    rules = (await client.get("/api/reference/rules", params={"submission": belarus})).json()
    by_code = by_destination(rules, countries)

    poland = by_code["PL"]
    assert poland["booking_type"] == "waiting_list"
    assert operators[poland["vac_operator_id"]] == "vfs"
    assert poland["identifier_type"] == "vac_id"

    france = by_code["FR"]
    assert france["booking_type"] == "windowed_application"
    assert france["window_days"] == 60
    assert france["identifier_type"] == "form_number"

    # "→ Germany": the Netherlands accept applications in Belarus through Germany.
    netherlands = by_code["NL"]
    assert netherlands["represented_by_id"] == await country_id(client, "DE")
    assert netherlands["vac_operator_id"] is None
    # Pairs the PRD marks "?" are kept, but as unverified.
    assert by_code["BE"]["is_verified"] is False


async def test_admin_adds_country_rule_and_visa_center(client: AsyncClient, make_user: MakeUser):
    await as_admin(client, make_user)
    country = await client.post(
        "/api/admin/reference/countries",
        json={"code": "jp", "name_ru": "Япония (тест)", "name_en": "Japan test"},
    )
    assert country.status_code == 409  # JP is seeded; codes are upper-cased first
    assert country.json()["detail"] == "country_code_taken"

    japan = await country_id(client, "JP")
    serbia = await country_id(client, "RS")
    created = await client.post(
        "/api/admin/reference/rules",
        json={
            "destination_id": japan,
            "submission_id": serbia,
            "booking_type": "embassy",
            "appeals_accepted": False,
        },
    )
    assert created.status_code == 201, created.text
    rule = created.json()
    assert rule["is_verified"] is False and rule["visa_centers"] == []

    center = await client.post(
        f"/api/admin/reference/rules/{rule['id']}/visa-centers",
        json={"city_ru": "Белград", "city_en": "Belgrade"},
    )
    assert center.status_code == 201
    again = await client.post(
        f"/api/admin/reference/rules/{rule['id']}/visa-centers",
        json={"city_ru": "Белград", "city_en": "Belgrade"},
    )
    assert again.status_code == 409 and again.json()["detail"] == "visa_center_exists"

    rules = (await client.get("/api/admin/reference/rules", params={"submission": serbia})).json()
    [listed] = [r for r in rules if r["id"] == rule["id"]]
    assert [c["city_en"] for c in listed["visa_centers"]] == ["Belgrade"]

    duplicate = await client.post(
        "/api/admin/reference/rules",
        json={"destination_id": japan, "submission_id": serbia, "booking_type": "direct_slot"},
    )
    assert duplicate.status_code == 409 and duplicate.json()["detail"] == "rule_exists"


async def test_windowed_application_needs_window_days(client: AsyncClient, make_user: MakeUser):
    await as_admin(client, make_user)
    japan = await country_id(client, "JP")
    serbia = await country_id(client, "RS")
    body = {
        "destination_id": japan,
        "submission_id": serbia,
        "booking_type": "windowed_application",
    }
    response = await client.post("/api/admin/reference/rules", json=body)
    assert response.status_code == 422
    assert response.json()["detail"] == "window_days_required"

    rule = (
        await client.post("/api/admin/reference/rules", json={**body, "window_days": 60})
    ).json()
    # Another booking type has no window: the old value is cleared, not left behind.
    changed = await client.patch(
        f"/api/admin/reference/rules/{rule['id']}", json={"booking_type": "waiting_list"}
    )
    assert changed.status_code == 200
    assert changed.json()["window_days"] is None

    back = await client.patch(
        f"/api/admin/reference/rules/{rule['id']}", json={"booking_type": "windowed_application"}
    )
    assert back.status_code == 422


async def test_appeal_deadline_only_when_appeals_accepted(client: AsyncClient, make_user: MakeUser):
    await as_admin(client, make_user)
    japan = await country_id(client, "JP")
    serbia = await country_id(client, "RS")
    rule = (
        await client.post(
            "/api/admin/reference/rules",
            json={
                "destination_id": japan,
                "submission_id": serbia,
                "booking_type": "direct_slot",
                "appeals_accepted": True,
                "appeal_deadline_days": 15,
            },
        )
    ).json()
    assert rule["appeal_deadline_days"] == 15
    changed = await client.patch(
        f"/api/admin/reference/rules/{rule['id']}", json={"appeals_accepted": False}
    )
    assert changed.json()["appeal_deadline_days"] is None


async def test_rule_cannot_point_a_country_at_itself(client: AsyncClient, make_user: MakeUser):
    await as_admin(client, make_user)
    belarus = await country_id(client, "BY")
    response = await client.post(
        "/api/admin/reference/rules",
        json={"destination_id": belarus, "submission_id": belarus, "booking_type": "embassy"},
    )
    assert response.status_code == 422


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


async def test_inactive_entries_are_hidden_from_workspaces(
    client: AsyncClient, make_user: MakeUser
):
    await as_admin(client, make_user)
    japan = await country_id(client, "JP")
    await client.patch(f"/api/admin/reference/countries/{japan}", json={"is_active": False})
    admin_codes = [c["code"] for c in (await client.get("/api/admin/reference/countries")).json()]
    assert "JP" in admin_codes

    poland = await country_id(client, "PL")
    await client.patch(f"/api/admin/reference/countries/{poland}", json={"is_active": False})

    client.cookies.clear()
    await make_user("anna@example.com")
    await login(client, "anna@example.com")
    codes = [c["code"] for c in (await client.get("/api/reference/countries")).json()]
    assert "JP" not in codes and "PL" not in codes and "FR" in codes
    # Rules of a switched-off country are hidden as well.
    belarus = await country_id(client, "BY")
    rules = (await client.get("/api/reference/rules", params={"submission": belarus})).json()
    assert poland not in {r["destination_id"] for r in rules}
    assert len(rules) == 25


async def test_rule_with_unknown_country_is_not_found(client: AsyncClient, make_user: MakeUser):
    await as_admin(client, make_user)
    belarus = await country_id(client, "BY")
    rules = (await client.get("/api/admin/reference/rules", params={"submission": belarus})).json()
    missing = "00000000-0000-0000-0000-000000000000"
    response = await client.patch(
        f"/api/admin/reference/rules/{rules[0]['id']}", json={"vac_operator_id": missing}
    )
    assert response.status_code == 404
    created = await client.post(
        "/api/admin/reference/rules",
        json={"destination_id": missing, "submission_id": belarus, "booking_type": "embassy"},
    )
    assert created.status_code == 404


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
        deleted = await session.execute(text("DELETE FROM visa_centers"))
        assert deleted.rowcount == 0
        with pytest.raises(DBAPIError):
            await session.execute(
                text(
                    "INSERT INTO countries (id, code, name_ru, name_en) "
                    "VALUES (gen_random_uuid(), 'XA', 'Тест', 'Test')"
                )
            )


async def test_no_scope_sees_no_reference(sessionmaker: async_sessionmaker[AsyncSession]):
    async with sessionmaker() as session:
        assert (await session.scalars(select(Country.code))).all() == []
