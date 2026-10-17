"""Document checklists (backlog 2.2): the shared list kept by the admin and a specialist's
own items on top of it."""

import uuid

from httpx import AsyncClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.core.db import scope_to_tenant
from app.models import OwnChecklistItem
from tests.helpers import MakeUser, login


async def turn_on(owner_engine: AsyncEngine, tenant_id: uuid.UUID | None) -> None:
    """Stage 2 modules are not in a new workspace by default: the admin turns them on."""
    async with owner_engine.begin() as conn:
        await conn.execute(
            text(
                "UPDATE tenants SET modules = array_append(modules, 'package_check') WHERE id = :t"
            ),
            {"t": tenant_id},
        )


async def as_specialist(
    client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine, email="anna@example.com"
) -> uuid.UUID | None:
    client.cookies.clear()
    tenant_id = await make_user(email)
    await turn_on(owner_engine, tenant_id)
    await login(client, email)
    return tenant_id


async def as_admin(client: AsyncClient, make_user: MakeUser) -> None:
    client.cookies.clear()
    await make_user("root@example.com", role="admin")
    await login(client, "root@example.com")


async def countries(client: AsyncClient) -> dict[str, str]:
    response = await client.get("/api/reference/countries")
    if response.status_code == 403:
        response = await client.get("/api/admin/reference/countries")
    return {c["code"]: c["id"] for c in response.json()}


async def add(client: AsyncClient, base: str, country: str, **fields) -> dict:
    response = await client.post(f"{base}/{country}/items", json=fields)
    assert response.status_code == 201, response.text
    return response.json()


async def test_admin_keeps_the_shared_checklist(client: AsyncClient, make_user: MakeUser):
    await as_admin(client, make_user)
    codes = await countries(client)
    base = "/api/admin/checklists"
    form = await add(client, base, codes["PL"], name="  Анкета  ")
    assert (form["name"], form["required"], form["purposes"]) == ("Анкета", True, [])
    work = await add(
        client,
        base,
        codes["PL"],
        name="Справка с места работы",
        note="Должность, стаж, зарплата; на бланке",
        required=False,
        max_age_days=30,
        purposes=["visit", "tourism", "visit"],
    )
    assert work["purposes"] == ["tourism", "visit"] and work["max_age_days"] == 30
    permit = await add(
        client, base, codes["PL"], name="Вид на жительство", submission_id=codes["TR"]
    )
    assert [i["sort_order"] for i in (form, work, permit)] == [0, 1, 2]

    changed = await client.patch(
        f"{base}/items/{work['id']}", json={"max_age_days": None, "note": " ", "is_active": False}
    )
    assert changed.status_code == 200, changed.text
    assert (changed.json()["max_age_days"], changed.json()["note"]) == (None, None)

    order = [permit["id"], form["id"], work["id"]]
    reordered = await client.put(f"{base}/{codes['PL']}/order", json={"ids": order})
    assert [i["id"] for i in reordered.json()] == order
    listed = (await client.get(f"{base}/{codes['PL']}")).json()
    assert [i["id"] for i in listed] == order  # switched-off items stay in the admin's list
    # Every item of the list, each once.
    for ids in (order[:2], [*order, order[0]]):
        wrong = await client.put(f"{base}/{codes['PL']}/order", json={"ids": ids})
        assert wrong.status_code == 422 and wrong.json()["detail"] == "order_mismatch"

    counts = (await client.get(base)).json()
    assert counts == [{"country_id": codes["PL"], "count": 2}]

    # Platform-level events, with the item's name only.
    events = (await client.get("/api/admin/audit")).json()["events"]
    created = [e for e in events if e["action"] == "reference.created"]
    assert {e["target_type"] for e in created} == {"checklist_item"}
    assert {e["details"]["name"] for e in created} == {
        "Анкета",
        "Справка с места работы",
        "Вид на жительство",
    }


async def test_items_are_checked(client: AsyncClient, make_user: MakeUser):
    await as_admin(client, make_user)
    codes = await countries(client)
    base = "/api/admin/checklists"
    for body in (
        {"name": ""},
        {"name": "Выписка", "max_age_days": 0},
        {"name": "Выписка", "max_age_days": 366},
        {"name": "Выписка", "purposes": ["holiday"]},
        {"name": "x" * 201},
    ):
        response = await client.post(f"{base}/{codes['PL']}/items", json=body)
        assert response.status_code == 422, body
    unknown = str(uuid.uuid4())
    assert (
        await client.post(f"{base}/{unknown}/items", json={"name": "Анкета"})
    ).status_code == 404
    response = await client.post(
        f"{base}/{codes['PL']}/items", json={"name": "Анкета", "submission_id": unknown}
    )
    assert response.status_code == 404


async def test_specialist_adds_own_items_on_top_of_the_shared(
    client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine
):
    await as_admin(client, make_user)
    codes = await countries(client)
    base = "/api/admin/checklists"
    await add(client, base, codes["PL"], name="Анкета")
    off = await add(client, base, codes["PL"], name="Старая справка")
    await client.patch(f"{base}/items/{off['id']}", json={"is_active": False})

    await as_specialist(client, make_user, owner_engine)
    own = await add(client, "/api/checklists", codes["PL"], name="Фото 3,5×4,5", max_age_days=180)
    second = await add(client, "/api/checklists", codes["PL"], name="Копии всех страниц")
    checklist = (await client.get(f"/api/checklists/{codes['PL']}")).json()
    # The shared items in use, then the specialist's own.
    assert [i["name"] for i in checklist["shared"]] == ["Анкета"]
    assert [i["name"] for i in checklist["own"]] == ["Фото 3,5×4,5", "Копии всех страниц"]

    switched = await client.patch(f"/api/checklists/items/{own['id']}", json={"is_active": False})
    assert switched.status_code == 200 and switched.json()["is_active"] is False
    order = [second["id"], own["id"]]
    assert (
        await client.put(f"/api/checklists/{codes['PL']}/order", json={"ids": order})
    ).status_code == 200
    checklist = (await client.get(f"/api/checklists/{codes['PL']}")).json()
    assert [i["id"] for i in checklist["own"]] == order

    counts = (await client.get("/api/checklists")).json()
    assert counts == [{"country_id": codes["PL"], "count": 2}]  # «Анкета» and the copies

    # A specialist does not change the shared list.
    shared_id = checklist["shared"][0]["id"]
    assert (
        await client.patch(f"/api/checklists/items/{shared_id}", json={"name": "X"})
    ).status_code == 404
    assert (await client.get("/api/admin/checklists")).status_code == 403

    # Her own items go to her workspace's log.
    events = (await client.get("/api/audit")).json()["events"]
    assert {e["action"] for e in events} >= {"reference.created", "reference.updated"}
    assert all(e["details"].get("name") != "Анкета" for e in events)


async def test_checklist_for_a_case_like_this(
    client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine
):
    await as_admin(client, make_user)
    codes = await countries(client)
    base = "/api/admin/checklists"
    await add(client, base, codes["PL"], name="Анкета")
    await add(client, base, codes["PL"], name="Приглашение", purposes=["visit"])
    await add(client, base, codes["PL"], name="ВНЖ Турции", submission_id=codes["TR"])

    await as_specialist(client, make_user, owner_engine)
    guest = await client.post(
        "/api/dictionaries/purposes", json={"name": "Гостевая", "kind": "visit"}
    )
    other = await client.post("/api/dictionaries/purposes", json={"name": "Шопинг"})
    assert guest.status_code == 201 and other.json()["kind"] is None
    await add(client, "/api/checklists", codes["PL"], name="Билеты", submission_id=codes["BY"])

    async def names(**params: str) -> list[str]:
        response = await client.get(f"/api/checklists/{codes['PL']}", params=params)
        assert response.status_code == 200, response.text
        return [i["name"] for part in ("shared", "own") for i in response.json()[part]]

    assert await names() == ["Анкета", "Приглашение", "ВНЖ Турции", "Билеты"]
    assert await names(submission=codes["BY"]) == ["Анкета", "Приглашение", "Билеты"]
    assert await names(submission=codes["TR"], purpose=guest.json()["id"]) == [
        "Анкета",
        "Приглашение",
        "ВНЖ Турции",
    ]
    # A purpose without a kind gets the items for any purpose only.
    assert await names(submission=codes["BY"], purpose=other.json()["id"]) == ["Анкета", "Билеты"]
    missing = await client.get(
        f"/api/checklists/{codes['PL']}", params={"purpose": str(uuid.uuid4())}
    )
    assert missing.status_code == 404


async def test_own_items_are_seen_in_their_workspace_only(
    client: AsyncClient,
    make_user: MakeUser,
    owner_engine: AsyncEngine,
    sessionmaker: async_sessionmaker[AsyncSession],
):
    anna = await as_specialist(client, make_user, owner_engine)
    codes = await countries(client)
    item = await add(client, "/api/checklists", codes["DE"], name="Страховка")

    vera = await as_specialist(client, make_user, owner_engine, "vera@example.com")
    checklist = (await client.get(f"/api/checklists/{codes['DE']}")).json()
    assert checklist == {"shared": [], "own": []}
    assert (await client.get("/api/checklists")).json() == []
    stolen = await client.patch(f"/api/checklists/items/{item['id']}", json={"name": "Моё"})
    assert stolen.status_code == 404

    async with sessionmaker() as session:
        scope_to_tenant(session, vera)
        assert (await session.scalars(select(OwnChecklistItem))).all() == []
    async with sessionmaker() as session:
        scope_to_tenant(session, anna)
        assert [i.name for i in await session.scalars(select(OwnChecklistItem))] == ["Страховка"]


async def test_checklists_need_their_module(
    client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine
):
    tenant_id = await make_user("vera@example.com")
    await login(client, "vera@example.com")
    denied = await client.get("/api/checklists")
    assert denied.status_code == 403 and denied.json()["detail"] == "module_disabled"

    # An employee with the module reads the lists; only the specialist changes them.
    await turn_on(owner_engine, tenant_id)
    client.cookies.clear()
    await make_user("olga@example.com", "employee", tenant_id=tenant_id)
    async with owner_engine.begin() as conn:
        await conn.execute(
            text("UPDATE users SET modules = '{package_check}' WHERE email = 'olga@example.com'")
        )
    await login(client, "olga@example.com")
    codes = await countries(client)
    assert (await client.get(f"/api/checklists/{codes['PL']}")).status_code == 200
    response = await client.post(f"/api/checklists/{codes['PL']}/items", json={"name": "Анкета"})
    assert response.status_code == 403
