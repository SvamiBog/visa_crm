"""Letting an employee go (backlog Т.15): the specialist sees which vault passwords the
employee revealed and whether each was changed since, to change the rest."""

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncEngine

from tests.helpers import MakeUser, give_cases, login
from tests.test_cases import new_contact, open_case
from tests.test_employee_cases import EMPLOYEE, switch_to, user_id, with_employee
from tests.test_vault import ENTRIES, entry, reveal, system


async def revealed(client: AsyncClient, owner_engine: AsyncEngine, email: str = EMPLOYEE):
    employee = await user_id(owner_engine, email)
    return await client.get(f"/api/employees/{employee}/revealed-passwords")


async def test_the_specialist_sees_what_to_change_after_an_employee(
    client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine
):
    await with_employee(client, make_user, owner_engine)
    anna = await new_contact(client, last_name="Ковалёва")
    await open_case(client, anna["id"])
    vfs = await system(client)
    gmail = await system(client, name="Gmail")
    kept = await entry(
        client, system_id=vfs["id"], contact_id=anna["id"], login="a@x.by", password="1"
    )
    changed = await entry(
        client, system_id=gmail["id"], contact_id=anna["id"], login="b@x.by", password="2"
    )
    gone = await entry(client, system_id=gmail["id"], login="own@x.by", password="3")
    untouched = await entry(
        client, system_id=vfs["id"], contact_id=anna["id"], login="c@x.by", password="4"
    )
    await reveal(client, untouched["id"])  # the specialist's own reveal does not count
    await give_cases(owner_engine, EMPLOYEE)

    await switch_to(client, EMPLOYEE)
    for entry_id in (kept["id"], kept["id"], changed["id"], gone["id"]):
        assert (await reveal(client, entry_id)).status_code == 200
    # Only the specialist sees the list.
    assert (await revealed(client, owner_engine)).status_code == 403

    await switch_to(client, "anna@example.com")
    assert (await client.patch(f"{ENTRIES}/{changed['id']}", json={"password": "new"})).is_success
    assert (await client.delete(f"{ENTRIES}/{gone['id']}")).status_code == 204
    response = await revealed(client, owner_engine)
    assert response.status_code == 200, response.text
    rows = {r["entry_id"]: r for r in response.json()}
    assert set(rows) == {kept["id"], changed["id"], gone["id"]}
    assert (rows[kept["id"]]["times"], rows[kept["id"]]["changed_since"]) == (2, False)
    assert rows[kept["id"]]["login"] == "a@x.by" and rows[kept["id"]]["contact_id"] == anna["id"]
    assert rows[kept["id"]]["system_name"] == "VFS Global"
    assert rows[changed["id"]]["changed_since"] is True
    assert rows[gone["id"]]["deleted"] is True and rows[gone["id"]]["system_name"] == "Gmail"
    # What still needs changing comes first.
    assert response.json()[0]["entry_id"] == kept["id"]


async def test_only_an_employee_of_the_workspace_has_the_list(
    client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine
):
    other = await make_user("boris@example.com")
    await make_user("ivan@example.com", "employee", tenant_id=other)
    await with_employee(client, make_user, owner_engine)
    assert (await revealed(client, owner_engine, "ivan@example.com")).status_code == 404
    assert (await revealed(client, owner_engine, "anna@example.com")).status_code == 404
    assert (await revealed(client, owner_engine)).json() == []
    client.cookies.clear()
    await login(client, "boris@example.com")
    assert (await revealed(client, owner_engine)).status_code == 404
