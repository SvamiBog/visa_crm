"""Files of the consent and the contract in the client's card (backlog 1.2): stored
encrypted, downloaded only by the workspace, every download in the audit log."""

import asyncio
import base64
import uuid
from collections.abc import Awaitable

import pytest
from httpx import AsyncClient, Response
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from app.api import documents as documents_api
from app.core import storage
from app.core.config import get_settings
from tests.support.helpers import MakeUser, give_cases, login
from tests.test_cases import open_case

PDF = b"%PDF-1.7\n1 0 obj << /Type /Catalog >> endobj\n%%EOF\n"
JPEG = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00" + b"\x11" * 64
GIB = 1024 * 1024 * 1024


async def as_specialist(client: AsyncClient, make_user: MakeUser, email="anna@example.com"):
    tenant_id = await make_user(email)
    await login(client, email)
    return tenant_id


async def contact(client: AsyncClient, name: str = "Ковалёва Анна") -> str:
    response = await client.post("/api/contacts", json={"first_name": name})
    assert response.status_code == 201, response.text
    return response.json()["id"]


async def upload(
    client: AsyncClient,
    contact_id: str,
    data: bytes = PDF,
    kind: str = "contract",
    name: str = "Договор Ковалёва.pdf",
):
    return await client.post(
        f"/api/contacts/{contact_id}/documents",
        json={"kind": kind, "file_name": name, "content": base64.b64encode(data).decode()},
    )


async def card(client: AsyncClient, contact_id: str) -> dict:
    response = await client.get(f"/api/contacts/{contact_id}")
    assert response.status_code == 200, response.text
    return response.json()


async def audit(client: AsyncClient) -> list[dict]:
    return (await client.get("/api/audit")).json()["events"]


async def test_file_is_kept_encrypted_and_downloaded_as_uploaded(
    client: AsyncClient, make_user: MakeUser
):
    tenant_id = await as_specialist(client, make_user)
    anna = await contact(client)
    response = await upload(client, anna)
    assert response.status_code == 201, response.text
    document = response.json()
    assert (document["kind"], document["file_name"]) == ("contract", "Договор Ковалёва.pdf")
    assert (document["content_type"], document["size_bytes"]) == ("application/pdf", len(PDF))
    assert document["uploaded_by_email"] == "anna@example.com"
    assert [d["id"] for d in (await card(client, anna))["documents"]] == [document["id"]]

    # The storage holds ciphertext under ids: no content, no name.
    [(key, blob)] = storage.MEMORY.items()
    assert key == f"documents/{tenant_id}/{document['id']}"
    assert PDF not in blob and "Ковалёва".encode() not in blob

    download = await client.get(f"/api/contacts/{anna}/documents/{document['id']}")
    assert download.status_code == 200
    assert download.content == PDF
    assert download.headers["content-type"] == "application/pdf"
    assert download.headers["content-disposition"].startswith("attachment;")
    assert download.headers["cache-control"] == "no-store"


async def test_adding_and_every_download_go_to_the_audit_log(
    client: AsyncClient, make_user: MakeUser
):
    await as_specialist(client, make_user)
    anna = await contact(client)
    document = (await upload(client, anna, JPEG, kind="consent", name="согласие.jpg")).json()
    for _ in range(2):
        assert (await client.get(f"/api/contacts/{anna}/documents/{document['id']}")).is_success
    events = [e for e in await audit(client) if e["action"].startswith("contacts.document")]
    assert [e["action"] for e in events] == [
        "contacts.document_opened",
        "contacts.document_opened",
        "contacts.document_added",
    ]
    for event in events:
        assert event["target_id"] == anna
        # The id and kind only: the file name may carry the client's name.
        assert event["details"] == {"document": document["id"], "kind": "consent"}


@pytest.mark.parametrize(
    ("data", "status_code", "code"),
    [
        (b"<html><script>alert(1)</script></html>", 422, "document_type_not_allowed"),
        (b"PK\x03\x04 a zip, a docx", 422, "document_type_not_allowed"),
        (b"", 422, "file_empty"),
        (PDF + b"\0" * (10 * 1024 * 1024), 413, "document_too_large"),
    ],
)
async def test_only_pdf_and_photos_up_to_10_mb(
    client: AsyncClient, make_user: MakeUser, data: bytes, status_code: int, code: str
):
    await as_specialist(client, make_user)
    anna = await contact(client)
    response = await upload(client, anna, data, name="contract.pdf")
    assert (response.status_code, response.json()["detail"]) == (status_code, code)
    assert storage.MEMORY == {}
    assert (await card(client, anna))["documents"] == []


async def test_name_is_cleaned_and_follows_the_content(client: AsyncClient, make_user: MakeUser):
    await as_specialist(client, make_user)
    anna = await contact(client)
    document = (await upload(client, anna, JPEG, name="C:\\scans\\page1.png")).json()
    assert (document["file_name"], document["content_type"]) == ("page1.png.jpg", "image/jpeg")


async def test_other_workspace_sees_no_files(client: AsyncClient, make_user: MakeUser):
    await as_specialist(client, make_user)
    anna = await contact(client)
    document = (await upload(client, anna)).json()
    client.cookies.clear()
    await as_specialist(client, make_user, "boris@example.com")
    url = f"/api/contacts/{anna}/documents/{document['id']}"
    assert (await client.get(url)).status_code == 404
    assert (await client.delete(url)).status_code == 404
    assert (await upload(client, anna)).status_code == 404
    # Nor through a contact of its own: the document is matched to its contact.
    own = await contact(client, "Борис")
    assert (await client.get(f"/api/contacts/{own}/documents/{document['id']}")).status_code == 404
    assert len(storage.MEMORY) == 1


async def test_employee_needs_the_clients_module_and_deletes_only_own_files(
    client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine
):
    tenant_id = await as_specialist(client, make_user)
    anna = await contact(client)
    specialists = (await upload(client, anna)).json()
    assert (await open_case(client, anna)).status_code == 201
    client.cookies.clear()
    await make_user("olga@example.com", "employee", tenant_id=tenant_id)
    await give_cases(owner_engine, "olga@example.com")
    await login(client, "olga@example.com")
    denied = await client.get(f"/api/contacts/{anna}/documents/{specialists['id']}")
    assert denied.status_code == 403 and denied.json()["detail"] == "module_disabled"

    async with owner_engine.begin() as conn:
        await conn.execute(
            text("UPDATE users SET modules = '{clients}' WHERE email = 'olga@example.com'")
        )
    assert (await client.get(f"/api/contacts/{anna}/documents/{specialists['id']}")).is_success
    forbidden = await client.delete(f"/api/contacts/{anna}/documents/{specialists['id']}")
    assert forbidden.status_code == 403
    own = (await upload(client, anna, JPEG, kind="consent")).json()
    assert (await client.delete(f"/api/contacts/{anna}/documents/{own['id']}")).status_code == 204

    client.cookies.clear()
    await login(client, "anna@example.com")
    url = f"/api/contacts/{anna}/documents/{specialists['id']}"
    assert (await client.delete(url)).status_code == 204
    assert (await card(client, anna))["documents"] == []
    assert storage.MEMORY == {}
    deleted = [e for e in await audit(client) if e["action"] == "contacts.document_deleted"]
    assert [e["actor_email"] for e in deleted] == ["anna@example.com", "olga@example.com"]


@pytest.fixture
def staging_without_storage(monkeypatch: pytest.MonkeyPatch) -> None:
    """The storage sees the staging stand without S3_BUCKET (the rest of the app stays in
    the test environment: a staging session cookie needs HTTPS)."""
    settings = get_settings().model_copy(update={"env": "staging", "s3_bucket": ""})
    monkeypatch.setattr(storage, "get_settings", lambda: settings)


async def test_without_storage_files_cannot_be_added(
    client: AsyncClient, make_user: MakeUser, staging_without_storage: None
):
    await as_specialist(client, make_user)
    workspace = (await client.get("/api/workspace")).json()
    assert workspace["file_storage"] is False
    anna = await contact(client)
    response = await upload(client, anna)
    assert (response.status_code, response.json()["detail"]) == (503, "storage_unavailable")
    assert (await card(client, anna))["documents"] == []


async def test_lost_object_is_reported_not_crashed(client: AsyncClient, make_user: MakeUser):
    await as_specialist(client, make_user)
    assert (await client.get("/api/workspace")).json()["file_storage"] is True
    anna = await contact(client)
    document = (await upload(client, anna)).json()
    storage.MEMORY.clear()
    response = await client.get(f"/api/contacts/{anna}/documents/{document['id']}")
    assert (response.status_code, response.json()["detail"]) == (502, "storage_failed")
    # The row of a lost object can still be removed.
    response = await client.delete(f"/api/contacts/{anna}/documents/{document['id']}")
    assert response.status_code == 204


async def test_unknown_contact_or_document(client: AsyncClient, make_user: MakeUser):
    await as_specialist(client, make_user)
    anna = await contact(client)
    assert (await upload(client, str(uuid.uuid4()))).status_code == 404
    missing = f"/api/contacts/{anna}/documents/{uuid.uuid4()}"
    assert (await client.get(missing)).status_code == 404
    assert (await client.delete(missing)).status_code == 404


async def test_a_file_for_a_card_deleted_meanwhile_leaves_no_object(
    client: AsyncClient,
    make_user: MakeUser,
    owner_engine: AsyncEngine,
    monkeypatch: pytest.MonkeyPatch,
):
    """The card is deleted as a duplicate while its first file is on its way to the
    storage (without a file it could be): the upload says the card is gone and takes the
    object back, so nothing is left in the storage that no row refers to."""
    await as_specialist(client, make_user)
    anna = await contact(client)

    async def put_while_the_card_goes(key: str, data: bytes) -> None:
        await storage.put_object(key, data)
        async with owner_engine.begin() as conn:
            await conn.execute(text("DELETE FROM contacts WHERE id = :c"), {"c": anna})

    monkeypatch.setattr(documents_api, "put_object", put_while_the_card_goes)
    response = await upload(client, anna)
    assert response.status_code == 404
    assert response.json()["detail"] == "not_found"
    assert storage.MEMORY == {}


# --- Quotas (backlog S12) --------------------------------------------------------------


async def as_member(
    client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine, role: str
) -> tuple[uuid.UUID, str, str]:
    """The workspace, Anna and Boris; logged in as the specialist, or as an employee with
    the clients module and Anna's case only: Boris is not theirs to see."""
    tenant_id = await as_specialist(client, make_user)
    anna = await contact(client)
    boris = await contact(client, "Борисов Борис")
    if role == "employee":
        assert (await open_case(client, anna)).status_code == 201
        client.cookies.clear()
        await make_user("olga@example.com", "employee", tenant_id=tenant_id)
        async with owner_engine.begin() as conn:
            await conn.execute(
                text("UPDATE users SET modules = '{clients}' WHERE email = 'olga@example.com'")
            )
        await give_cases(owner_engine, "olga@example.com")
        await login(client, "olga@example.com")
        assert (await client.get(f"/api/contacts/{boris}")).status_code == 404
    return tenant_id, anna, boris


async def stored(
    owner_engine: AsyncEngine, tenant_id: uuid.UUID, contact_id: str, count: int, size: int
) -> None:
    """Rows of files kept earlier, straight in the database: only the quota reads them."""
    async with owner_engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO contact_documents "
                "(id, tenant_id, contact_id, kind, file_name, content_type, size_bytes) "
                "SELECT gen_random_uuid(), CAST(:tenant AS uuid), CAST(:contact AS uuid), "
                "'contract', 'old.pdf', 'application/pdf', CAST(:size AS integer) "
                "FROM generate_series(1, CAST(:count AS integer))"
            ),
            {"tenant": str(tenant_id), "contact": contact_id, "size": size, "count": count},
        )


async def fill_workspace(
    owner_engine: AsyncEngine, tenant_id: uuid.UUID, contact_id: str, free: int
) -> None:
    """Files of `contact_id` that leave `free` bytes of the workspace's space (a row holds
    at most 2 GiB)."""
    whole, rest = divmod(documents_api.MAX_WORKSPACE_BYTES - free, GIB)
    await stored(owner_engine, tenant_id, contact_id, whole, GIB)
    await stored(owner_engine, tenant_id, contact_id, 1 if rest else 0, rest)


@pytest.fixture
def sent(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Keys of the objects uploads sent to the storage, kept or not."""
    keys: list[str] = []

    async def put(key: str, data: bytes) -> None:
        keys.append(key)
        await storage.put_object(key, data)

    monkeypatch.setattr(documents_api, "put_object", put)
    return keys


@pytest.mark.parametrize("role", ["specialist", "employee"])
async def test_a_client_keeps_at_most_50_files(
    client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine, sent: list[str], role: str
):
    tenant_id, anna, _ = await as_member(client, make_user, owner_engine, role)
    assert documents_api.MAX_DOCUMENTS_PER_CONTACT == 50
    await stored(owner_engine, tenant_id, anna, 49, len(PDF))
    last = await upload(client, anna)
    assert last.status_code == 201, last.text
    refused = await upload(client, anna, JPEG, kind="consent")
    assert (refused.status_code, refused.json().get("detail")) == (422, "too_many_documents")
    # Refused before the storage: the file was neither sent nor kept.
    assert sent == [f"documents/{tenant_id}/{last.json()['id']}"]
    assert list(storage.MEMORY) == sent
    assert len((await card(client, anna))["documents"]) == 50

    # A file removed frees its place.
    removed = await client.delete(f"/api/contacts/{anna}/documents/{last.json()['id']}")
    assert removed.status_code == 204
    assert (await upload(client, anna, JPEG, kind="consent")).status_code == 201


@pytest.mark.parametrize("role", ["specialist", "employee"])
async def test_files_of_a_workspace_take_at_most_10_gib_counting_every_client(
    client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine, sent: list[str], role: str
):
    tenant_id, anna, boris = await as_member(client, make_user, owner_engine, role)
    assert documents_api.MAX_WORKSPACE_BYTES == 10 * GIB
    # Boris's files count even for an employee, who does not see him.
    await fill_workspace(owner_engine, tenant_id, boris, free=len(PDF))
    last = await upload(client, anna)
    assert last.status_code == 201, last.text
    refused = await upload(client, anna, JPEG, kind="consent")
    assert (refused.status_code, refused.json().get("detail")) == (413, "documents_storage_full")
    # Refused before the storage: the file was neither sent nor kept.
    assert sent == [f"documents/{tenant_id}/{last.json()['id']}"]
    assert list(storage.MEMORY) == sent
    assert [d["id"] for d in (await card(client, anna))["documents"]] == [last.json()["id"]]


async def _all_waiting(conn: AsyncConnection, count: int) -> bool:
    """Do `count` connections to this database wait for a lock? Asked again and again for
    up to ten seconds; the activity view is read afresh each time."""
    for _ in range(200):
        await conn.execute(text("SELECT pg_stat_clear_snapshot()"))
        waiting = await conn.scalar(
            text(
                "SELECT count(*) FROM pg_stat_activity "
                "WHERE datname = current_database() AND wait_event_type = 'Lock'"
            )
        )
        if waiting >= count:
            return True
        await asyncio.sleep(0.05)
    return False


async def at_once_while_files_wait(
    owner_engine: AsyncEngine, calls: list[Awaitable[Response]]
) -> list[Response]:
    """Send uploads at once while the files table takes no new rows, as in a slow moment
    of the database: each upload counts the files and waits to add its row, or waits for
    another upload. The table opens once every upload waits, so timing does not decide
    the outcome: if two uploads can count the same files, they all pass."""
    async with owner_engine.begin() as conn:
        await conn.execute(text("LOCK TABLE contact_documents IN SHARE MODE"))
        answers = asyncio.gather(*calls)
        reached = await _all_waiting(conn, len(calls))
    assert reached, "the uploads never reached the table"
    return await answers


@pytest.mark.parametrize(
    ("full", "status_code", "code"),
    [("client", 422, "too_many_documents"), ("workspace", 413, "documents_storage_full")],
)
async def test_uploads_sent_at_once_stay_within_the_quota(
    client: AsyncClient,
    make_user: MakeUser,
    owner_engine: AsyncEngine,
    full: str,
    status_code: int,
    code: str,
):
    tenant_id, anna, boris = await as_member(client, make_user, owner_engine, "specialist")
    if full == "client":
        await stored(owner_engine, tenant_id, anna, 49, len(PDF))
    else:
        await fill_workspace(owner_engine, tenant_id, boris, free=len(PDF))
    answers = await at_once_while_files_wait(owner_engine, [upload(client, anna) for _ in range(3)])
    outcome = sorted((a.status_code, a.json().get("detail")) for a in answers)
    assert outcome == [(201, None), (status_code, code), (status_code, code)]
    [added] = [answer.json() for answer in answers if answer.status_code == 201]
    # What a refused upload had sent is gone from the storage.
    assert list(storage.MEMORY) == [f"documents/{tenant_id}/{added['id']}"]
