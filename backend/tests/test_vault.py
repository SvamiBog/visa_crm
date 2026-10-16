"""The vault of accounts (backlog 1.7): systems with password rules, accounts with
encrypted passwords, showing a password only on request and logging every showing."""

import uuid

from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from tests.helpers import MakeUser, login

SYSTEMS = "/api/dictionaries/vault-systems"
ENTRIES = "/api/vault/entries"


async def as_specialist(client: AsyncClient, make_user: MakeUser, email="anna@example.com"):
    tenant_id = await make_user(email)
    await login(client, email)
    return tenant_id


async def system(client: AsyncClient, name: str = "VFS Global", **rules) -> dict:
    response = await client.post(SYSTEMS, json={"name": name, "kind": "visa_center", **rules})
    assert response.status_code == 201, response.text
    return response.json()


async def contact(client: AsyncClient, name: str = "Анна") -> str:
    response = await client.post("/api/contacts", json={"full_name": name})
    assert response.status_code == 201, response.text
    return response.json()["id"]


async def entry(client: AsyncClient, **body) -> dict:
    response = await client.post(ENTRIES, json=body)
    assert response.status_code == 201, response.text
    return response.json()


async def reveal(client: AsyncClient, entry_id: str):
    return await client.post(f"{ENTRIES}/{entry_id}/reveal")


async def audit(client: AsyncClient) -> list[dict]:
    return (await client.get("/api/audit")).json()["events"]


async def test_systems_keep_password_rules(client: AsyncClient, make_user: MakeUser):
    await as_specialist(client, make_user)
    vfs = await system(
        client, url="https://visa.vfsglobal.com", password_length=15, password_symbols="$@#$"
    )
    assert vfs["password_length"] == 15
    assert vfs["password_symbols"] == "$@#"  # each character once

    updated = await client.patch(
        f"{SYSTEMS}/{vfs['id']}", json={"password_symbols": "", "url": " "}
    )
    assert updated.status_code == 200, updated.text
    assert (updated.json()["password_symbols"], updated.json()["url"]) == ("", None)
    nothing = await client.patch(
        f"{SYSTEMS}/{vfs['id']}",
        json={"password_lower": False, "password_upper": False, "password_digits": False},
    )
    assert nothing.status_code == 422 and nothing.json()["detail"] == "password_rules_empty"
    letters = await client.post(
        SYSTEMS, json={"name": "X", "kind": "other", "password_symbols": "ab"}
    )
    assert letters.status_code == 422
    taken = await client.post(SYSTEMS, json={"name": "VFS Global", "kind": "other"})
    assert taken.status_code == 409 and taken.json()["detail"] == "name_taken"
    assert [s["name"] for s in (await client.get(SYSTEMS)).json()] == ["VFS Global"]


async def test_password_is_stored_encrypted_and_shown_on_request_with_a_log_entry(
    client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine
):
    await as_specialist(client, make_user)
    vfs = await system(client)
    anna = await contact(client)
    created = await entry(
        client,
        system_id=vfs["id"],
        contact_id=anna,
        login=" anna.k.pl@mail.by ",
        email_owner="specialist",
        password="Secret-Pass 42",
        extra_id="AB12",
        created_manually=True,
        note=" ",
    )
    assert created["login"] == "anna.k.pl@mail.by"
    assert created["has_password"] and created["password_updated_at"]
    assert created["note"] is None
    assert "password" not in created and "password_encrypted" not in created

    async with owner_engine.connect() as conn:
        stored = await conn.scalar(
            text("SELECT password_encrypted FROM vault_entries WHERE id = :id"),
            {"id": created["id"]},
        )
    assert stored and b"Secret-Pass" not in stored

    listed = (await client.get(f"/api/contacts/{anna}/vault")).json()
    assert [e["id"] for e in listed] == [created["id"]]
    assert "password" not in listed[0]

    shown = await reveal(client, created["id"])
    assert shown.status_code == 200
    assert shown.json() == {"password": "Secret-Pass 42"}  # spaces are kept
    assert shown.headers["cache-control"] == "no-store"
    event = (await audit(client))[0]
    assert event["action"] == "vault.password_revealed"
    assert (event["target_type"], event["target_id"]) == ("vault_entry", created["id"])
    # The system's name and ids only: never the login or the password.
    assert event["details"] == {"name": "VFS Global", "contact": anna}


async def test_changing_and_removing_a_password(client: AsyncClient, make_user: MakeUser):
    await as_specialist(client, make_user)
    vfs = await system(client)
    anna, boris = await contact(client), await contact(client, "Борис")
    created = await entry(
        client, system_id=vfs["id"], contact_id=anna, login="a@mail.by", password="one"
    )
    url = f"{ENTRIES}/{created['id']}"

    changed = await client.patch(url, json={"password": "two", "login": None})
    assert changed.status_code == 200 and changed.json()["login"] == "a@mail.by"
    assert (await reveal(client, created["id"])).json()["password"] == "two"
    updated = [e for e in await audit(client) if e["action"] == "vault.entry_updated"]
    assert updated[0]["details"] == {"name": "VFS Global", "contact": anna, "fields": "password"}

    removed = await client.patch(url, json={"password": None})
    assert not removed.json()["has_password"] and removed.json()["password_updated_at"] is None
    none = await reveal(client, created["id"])
    assert none.status_code == 409 and none.json()["detail"] == "no_password"

    # A case of another applicant cannot hold this account.
    codes = {c["code"]: c["id"] for c in (await client.get("/api/reference/countries")).json()}
    [borises] = (
        await client.post(
            f"/api/contacts/{boris}/cases",
            json={
                "destination_id": codes["PL"],
                "submission_country_id": codes["BY"],
                "applicants": [{"contact_id": boris}],
            },
        )
    ).json()
    wrong = await client.patch(url, json={"case_id": borises["id"]})
    assert wrong.status_code == 422 and wrong.json()["detail"] == "case_of_another_contact"

    deleted = await client.delete(url)
    assert deleted.status_code == 204
    assert (await client.get(f"/api/contacts/{anna}/vault")).json() == []
    assert (await audit(client))[0]["action"] == "vault.entry_deleted"


async def test_own_mailbox_lists_the_accounts_made_on_it(client: AsyncClient, make_user: MakeUser):
    await as_specialist(client, make_user)
    mail = await system(client, "Почта", password_length=10)
    vfs = await system(client)
    anna, boris = await contact(client), await contact(client, "Борис")
    mailbox = await entry(client, system_id=mail["id"], login="Backup.Visa@mail.by", password="x")
    assert mailbox["contact_id"] is None
    for person in (boris, anna):
        await entry(
            client,
            system_id=vfs["id"],
            contact_id=person,
            login="backup.visa@mail.by",
            email_owner="specialist_backup",
        )
    await entry(client, system_id=vfs["id"], contact_id=anna, login="anna@mail.by")

    [own] = (await client.get("/api/vault/own")).json()
    assert own["id"] == mailbox["id"]
    assert [a["contact_name"] for a in own["accounts"]] == ["Анна", "Борис"]

    # The workspace's own account belongs to no case.
    codes = {c["code"]: c["id"] for c in (await client.get("/api/reference/countries")).json()}
    [case] = (
        await client.post(
            f"/api/contacts/{anna}/cases",
            json={
                "destination_id": codes["PL"],
                "submission_country_id": codes["BY"],
                "applicants": [{"contact_id": anna}],
            },
        )
    ).json()
    orphan = await client.post(
        ENTRIES, json={"system_id": mail["id"], "login": "x@mail.by", "case_id": case["id"]}
    )
    assert orphan.status_code == 422
    # A case's application email can be on the backup mailbox too.
    owned = await client.patch(
        f"/api/cases/{case['id']}",
        json={
            "application_email": "backup.visa@mail.by",
            "application_email_owner": "specialist_backup",
        },
    )
    assert owned.status_code == 200, owned.text


async def test_client_is_found_by_the_login_of_an_account(client: AsyncClient, make_user: MakeUser):
    await as_specialist(client, make_user)
    vfs = await system(client)
    anna = await contact(client)
    await contact(client, "Борис")
    await entry(client, system_id=vfs["id"], contact_id=anna, login="anna.k.pl@mail.by")
    found = await client.post("/api/contacts/search", json={"query": "k.pl@mail"})
    assert [c["id"] for c in found.json()["items"]] == [anna]


async def test_generator_follows_the_rules_of_the_site(client: AsyncClient, make_user: MakeUser):
    await as_specialist(client, make_user)
    pin = await system(
        client,
        "PIN",
        password_length=8,
        password_lower=False,
        password_upper=False,
        password_symbols="",
    )
    response = await client.post("/api/vault/password", json={"system_id": pin["id"]})
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    password = response.json()["password"]
    assert len(password) == 8 and password.isdigit()
    default = (await client.post("/api/vault/password", json={})).json()["password"]
    assert len(default) == 14
    # Generating is not showing a stored password: nothing goes to the log.
    assert not [e for e in await audit(client) if e["action"].startswith("vault.")]


async def test_employee_sees_the_vault_only_when_given(
    client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine
):
    tenant_id = await as_specialist(client, make_user)
    vfs = await system(client)
    anna = await contact(client)
    created = await entry(
        client, system_id=vfs["id"], contact_id=anna, login="a@mail.by", password="pw"
    )
    client.cookies.clear()
    await make_user("olga@example.com", "employee", tenant_id=tenant_id)
    async with owner_engine.begin() as conn:
        await conn.execute(
            text("UPDATE users SET modules = '{clients,cases}' WHERE email = 'olga@example.com'")
        )
    await login(client, "olga@example.com")
    denied = await client.get(f"/api/contacts/{anna}/vault")
    assert denied.status_code == 403 and denied.json()["detail"] == "module_disabled"
    assert (await reveal(client, created["id"])).status_code == 403
    # Nor is a client found by an account's login.
    found = await client.post("/api/contacts/search", json={"query": "a@mail.by"})
    assert found.json()["items"] == []

    async with owner_engine.begin() as conn:
        await conn.execute(
            text(
                "UPDATE users SET modules = '{clients,cases,vault}' "
                "WHERE email = 'olga@example.com'"
            )
        )
    assert (await reveal(client, created["id"])).json()["password"] == "pw"
    client.cookies.clear()
    await login(client, "anna@example.com")
    [event] = [e for e in await audit(client) if e["action"] == "vault.password_revealed"]
    assert (event["action"], event["actor_email"]) == (
        "vault.password_revealed",
        "olga@example.com",
    )


async def test_another_workspace_cannot_reach_the_accounts(
    client: AsyncClient, make_user: MakeUser
):
    await as_specialist(client, make_user)
    vfs = await system(client)
    anna = await contact(client)
    created = await entry(
        client, system_id=vfs["id"], contact_id=anna, login="a@mail.by", password="pw"
    )
    client.cookies.clear()
    await as_specialist(client, make_user, "boris@example.com")
    assert (await reveal(client, created["id"])).status_code == 404
    assert (await client.patch(f"{ENTRIES}/{created['id']}", json={"note": "x"})).status_code == 404
    assert (await client.get(f"/api/contacts/{anna}/vault")).status_code == 404
    assert (await client.get("/api/vault/own")).json() == []
    # Nor can it use the other workspace's system.
    foreign = await client.post(ENTRIES, json={"system_id": vfs["id"], "login": "b@mail.by"})
    assert foreign.status_code == 404
    unknown = await client.post(ENTRIES, json={"system_id": str(uuid.uuid4()), "login": "b"})
    assert unknown.status_code == 404
