"""Files of the consent and the contract in the client's card (backlog 1.2): stored
encrypted, downloaded only by the workspace, every download in the audit log."""

import base64
import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core import storage
from app.core.config import get_settings
from tests.helpers import MakeUser, give_cases, login
from tests.test_cases import open_case

PDF = b"%PDF-1.7\n1 0 obj << /Type /Catalog >> endobj\n%%EOF\n"
JPEG = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00" + b"\x11" * 64


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
