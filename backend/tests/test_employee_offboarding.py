"""Letting an employee go (backlog Т.15): the specialist sees which vault passwords the
employee revealed, added or set, and whether someone else changed each since, to change
the rest (audit 2026-11, backlog S3)."""

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
    # The account on the site outlives the entry: a deleted one is still to change.
    assert (rows[gone["id"]]["deleted"], rows[gone["id"]]["changed_since"]) == (True, False)
    assert rows[gone["id"]]["system_name"] == "Gmail"
    # What still needs changing comes first.
    assert response.json()[-1]["entry_id"] == changed["id"]


async def test_a_password_the_employee_saved_themselves_is_still_to_change(
    client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine
):
    await with_employee(client, make_user, owner_engine)
    anna = await new_contact(client, last_name="Ковалёва")
    await open_case(client, anna["id"])
    vfs = await system(client)
    account = await entry(
        client, system_id=vfs["id"], contact_id=anna["id"], login="a@x.by", password="1"
    )
    url = f"{ENTRIES}/{account['id']}"
    await give_cases(owner_engine, EMPLOYEE)

    async def row() -> dict:
        await switch_to(client, "anna@example.com")
        [only] = (await revealed(client, owner_engine)).json()
        return only

    await switch_to(client, EMPLOYEE)
    seen = (await reveal(client, account["id"])).json()["password"]
    assert (await client.patch(url, json={"password": seen})).status_code == 200
    # Saving the same password again changes nothing on the site.
    now = await row()
    assert (now["changed_since"], now["deleted"]) == (False, False)
    assert (await reveal(client, account["id"])).json()["password"] == seen

    # Nor does a new one the employee sets: they know that one too.
    await switch_to(client, EMPLOYEE)
    assert (await client.patch(url, json={"password": "mine"})).status_code == 200
    assert (await row())["changed_since"] is False

    # One the specialist sets after the employee saw it is changed.
    assert (await client.patch(url, json={"password": "fresh"})).status_code == 200
    assert (await row())["changed_since"] is True

    # Seen again, it is to change again; taking it out of the vault changes nothing on
    # the site either.
    await switch_to(client, EMPLOYEE)
    assert (await reveal(client, account["id"])).status_code == 200
    await switch_to(client, "anna@example.com")
    assert (await client.patch(url, json={"password": None})).status_code == 200
    now = await row()
    assert (now["times"], now["changed_since"]) == (2, False)


async def test_an_account_deleted_after_the_employee_saw_it_is_still_to_change(
    client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine
):
    await with_employee(client, make_user, owner_engine)
    anna = await new_contact(client, last_name="Ковалёва")
    await open_case(client, anna["id"])
    vfs = await system(client)
    theirs = await entry(
        client, system_id=vfs["id"], contact_id=anna["id"], login="a@x.by", password="1"
    )
    await give_cases(owner_engine, EMPLOYEE)

    await switch_to(client, EMPLOYEE)
    assert (await reveal(client, theirs["id"])).status_code == 200
    # An employee deletes only the accounts they added (test_vault.py).
    assert (await client.delete(f"{ENTRIES}/{theirs['id']}")).status_code == 403
    own = await entry(
        client, system_id=vfs["id"], contact_id=anna["id"], login="b@x.by", password="2"
    )
    assert (await client.delete(f"{ENTRIES}/{own['id']}")).status_code == 204

    await switch_to(client, "anna@example.com")
    assert (await client.delete(f"{ENTRIES}/{theirs['id']}")).status_code == 204
    response = await revealed(client, owner_engine)
    assert response.status_code == 200, response.text
    rows = {r["entry_id"]: r for r in response.json()}
    assert set(rows) == {theirs["id"], own["id"]}
    for row in rows.values():
        # The site account is still there; the log names its client and site, never
        # its login.
        assert (row["deleted"], row["changed_since"]) == (True, False)
        assert (row["contact_id"], row["contact_name"]) == (anna["id"], anna["full_name"])
        assert (row["system_name"], row["login"]) == ("VFS Global", None)
    assert (rows[theirs["id"]]["times"], rows[own["id"]]["times"]) == (1, 0)


async def test_accounts_the_employee_added_or_set_a_password_on_are_listed(
    client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine
):
    await with_employee(client, make_user, owner_engine)
    anna = await new_contact(client, last_name="Ковалёва")
    await open_case(client, anna["id"])
    vfs = await system(client)
    given = await entry(client, system_id=vfs["id"], contact_id=anna["id"], login="a@x.by")
    await entry(client, system_id=vfs["id"], contact_id=anna["id"], login="b@x.by", password="1")
    await give_cases(owner_engine, EMPLOYEE)

    await switch_to(client, EMPLOYEE)
    # Never shown, but the employee typed these passwords.
    added = await entry(
        client, system_id=vfs["id"], contact_id=anna["id"], login="c@x.by", password="2"
    )
    assert (await client.patch(f"{ENTRIES}/{given['id']}", json={"password": "3"})).is_success
    # A note is not a password.
    assert (await client.patch(f"{ENTRIES}/{added['id']}", json={"note": "x"})).is_success

    await switch_to(client, "anna@example.com")
    response = await revealed(client, owner_engine)
    assert response.status_code == 200, response.text
    rows = {r["entry_id"]: r for r in response.json()}
    assert set(rows) == {added["id"], given["id"]}
    for row in rows.values():
        assert (row["times"], row["changed_since"], row["deleted"]) == (0, False, False)
    assert (rows[added["id"]]["login"], rows[given["id"]]["login"]) == ("c@x.by", "a@x.by")


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
