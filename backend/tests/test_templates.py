"""Document templates (backlog 2.1): the specialist's Word files with codes, given to a
client and downloaded filled with the client's data, named for the client."""

import base64
import uuid
from urllib.parse import unquote

import pytest
from httpx import AsyncClient, Response
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core import storage
from app.core.config import get_settings
from tests.helpers import MakeUser, login
from tests.test_docx_fill import make_docx, paragraphs, runs

LETTER = make_docx(
    runs("Я, {{ФИО}}, ", "{{Дата ", "рождения}}, паспорт {{Номер паспорта}}.")
    + runs("Страна: {{Страна визы}}; {{Visa country}}; цель: {{Цель}}.")
    + runs("Стоимость: {{Стоимость}}. Телефон: {{Телефон}}. {{Фамлия}}"),
)


async def turn_on(owner_engine: AsyncEngine, tenant_id: uuid.UUID | None) -> None:
    """Stage 2 modules are not in a new workspace by default: the admin turns them on."""
    async with owner_engine.begin() as conn:
        await conn.execute(
            text(
                "UPDATE tenants SET modules = array_append(modules, 'document_templates') "
                "WHERE id = :t"
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


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


async def upload(client: AsyncClient, data: bytes = LETTER, **fields) -> Response:
    body = {"source_name": "Спонсорское письмо.docx", "content": b64(data), **fields}
    return await client.post("/api/templates", json=body)


async def new_client(client: AsyncClient, **fields) -> dict:
    response = await client.post(
        "/api/contacts",
        json={
            "last_name": "Ковалёва",
            "first_name": "Анна",
            "birth_date": "1990-10-05",
            "phone": "+375291234567",
            "passport": {"passport_number": "MP1234567"},
            **fields,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


async def open_case(client: AsyncClient, contact: dict) -> dict:
    codes = {c["code"]: c["id"] for c in (await client.get("/api/reference/countries")).json()}
    purpose = await client.post(
        "/api/dictionaries/purposes", json={"name": "Туризм", "kind": "tourism"}
    )
    response = await client.post(
        f"/api/contacts/{contact['id']}/cases",
        json={
            "destination_id": codes["PL"],
            "submission_country_id": codes["BY"],
            "purpose_id": purpose.json()["id"],
            "applicants": [{"contact_id": contact["id"]}],
        },
    )
    assert response.status_code == 201, response.text
    case = response.json()[0]
    priced = await client.patch(f"/api/cases/{case['id']}/finance", json={"price": "1350"})
    assert priced.status_code == 200, priced.text
    return case


def file_name_of(response: Response) -> str:
    return unquote(response.headers["content-disposition"].split("filename*=UTF-8''", 1)[1])


async def audit(client: AsyncClient) -> list[dict]:
    return (await client.get("/api/audit")).json()["events"]


async def test_a_template_is_filled_for_the_client(
    client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine
):
    tenant_id = await as_specialist(client, make_user, owner_engine)
    response = await upload(client, file_name="Письмо {{ФИО}} {{Дата подачи}}")
    assert response.status_code == 201, response.text
    template = response.json()
    assert template["name"] == "Спонсорское письмо"  # the file's name without .docx
    assert template["codes"] == [
        "full_name",
        "birth_date",
        "passport_number",
        "destination",
        "purpose",
        "price",
        "phone",
    ]
    assert template["unknown_codes"] == ["Фамлия"]
    # The storage holds ciphertext under ids: no content, no name.
    [(key, blob)] = storage.MEMORY.items()
    assert key == f"templates/{tenant_id}/{template['id']}"
    assert LETTER not in blob and "Спонсорское".encode() not in blob

    anna = await new_client(client)
    case = await open_case(client, anna)
    url = f"/api/contacts/{anna['id']}/templates"
    given = await client.post(url, json={"template_id": template["id"], "case_id": case["id"]})
    assert given.status_code == 201, given.text
    for_client = await client.post(url, json={"template_id": template["id"]})
    assert for_client.status_code == 201
    again = await client.post(url, json={"template_id": template["id"], "case_id": case["id"]})
    assert (again.status_code, again.json()["detail"]) == (409, "template_already_given")

    listed = (await client.get(url)).json()
    assert [g["case_id"] for g in listed] == [None, case["id"]]  # newest first
    without_case, with_case = listed
    # Named by the pattern; what the codes do not have yet is shown before the download.
    assert with_case["file_name"] == "Письмо Ковалёва Анна.docx"
    assert with_case["missing"] == []
    assert without_case["missing"] == ["destination", "purpose", "price"]
    assert with_case["unknown_codes"] == ["Фамлия"]

    filled = await client.get(f"{url}/{given.json()['id']}/file")
    assert filled.status_code == 200, filled.text
    assert filled.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )
    assert file_name_of(filled) == "Письмо Ковалёва Анна.docx"
    assert paragraphs(filled.content) == [
        "Я, Ковалёва Анна, 05.10.1990, паспорт MP1234567.",
        "Страна: Польша; Poland; цель: Туризм.",
        "Стоимость: 1 350,00 BYN. Телефон: +375291234567. {{Фамлия}}",
    ]
    general = await client.get(f"{url}/{for_client.json()['id']}/file")
    assert paragraphs(general.content)[1] == "Страна: ; ; цель: ."

    # The blank template comes back as it was uploaded.
    blank = await client.get(f"/api/templates/{template['id']}/file")
    assert blank.content == LETTER and file_name_of(blank) == "Спонсорское письмо.docx"

    # Downloads are in the log with ids only: the file carries the client's data.
    events = await audit(client)
    opened = [e for e in events if e["action"] == "contacts.template_opened"]
    assert sorted(len(e["details"]) for e in opened) == [1, 2]
    assert {"template": template["id"], "case": case["id"]} in [e["details"] for e in opened]
    assert all("Ковалёва" not in str(e["details"]) for e in events)
    assert {"templates.uploaded", "contacts.template_added"} <= {e["action"] for e in events}


async def test_the_name_and_the_passport_fill_in_parts(
    client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine
):
    await as_specialist(client, make_user, owner_engine)
    form = make_docx(
        runs("{{Фамилия}}, {{Имя}} {{Отчество}}; {{Surname latin}} / {{Given names latin}}.")
        + runs("Выдан: {{Кем выдан}}, родилась: {{Место рождения}}.")
    )
    template = (await upload(client, form)).json()
    assert template["unknown_codes"] == []
    anna = await new_client(
        client,
        middle_name="Сергеевна",
        last_name_latin="KAVALEVA",
        first_name_latin="ANNA",
        passport={"passport_issued_by": "Министерство внутренних дел", "birth_place": "г. Минск"},
    )
    url = f"/api/contacts/{anna['id']}/templates"
    given = (await client.post(url, json={"template_id": template["id"]})).json()
    filled = await client.get(f"{url}/{given['id']}/file")
    assert paragraphs(filled.content) == [
        "Ковалёва, Анна Сергеевна; KAVALEVA / ANNA.",
        "Выдан: Министерство внутренних дел, родилась: г. Минск.",
    ]


async def test_the_specialist_keeps_her_templates(
    client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine
):
    tenant_id = await as_specialist(client, make_user, owner_engine)
    template = (await upload(client, name=" Письмо ")).json()
    assert template["name"] == "Письмо"
    anna = await new_client(client)
    url = f"/api/contacts/{anna['id']}/templates"
    given = (await client.post(url, json={"template_id": template["id"]})).json()

    # A typo in the file name pattern is refused; the default name is the template's and
    # the client's.
    wrong = await client.patch(f"/api/templates/{template['id']}", json={"file_name": "{{Фио2}}"})
    assert (wrong.status_code, wrong.json()["detail"]) == (422, "unknown_code_in_name")
    renamed = await client.patch(
        f"/api/templates/{template['id']}", json={"name": "Cover letter", "file_name": " "}
    )
    assert renamed.json()["file_name"] is None and renamed.json()["clients"] == 1
    assert (await client.get(url)).json()[0]["file_name"] == "Cover letter — Ковалёва Анна.docx"

    # A new version of the file: the clients who have the template get it.
    newer = make_docx(runs("{{Full name}}, born {{date of birth}}"))
    replaced = await client.put(
        f"/api/templates/{template['id']}/file",
        json={"source_name": "letter.docx", "content": b64(newer)},
    )
    assert replaced.status_code == 200, replaced.text
    assert replaced.json()["codes"] == ["full_name", "birth_date"]
    assert replaced.json()["unknown_codes"] == []
    filled = await client.get(f"{url}/{given['id']}/file")
    assert paragraphs(filled.content) == ["Ковалёва Анна, born 05.10.1990"]

    # Switched off: not given to new clients, still there for those who have it.
    await client.patch(f"/api/templates/{template['id']}", json={"is_active": False})
    vera = (await client.post("/api/contacts", json={"first_name": "Вера"})).json()
    refused = await client.post(
        f"/api/contacts/{vera['id']}/templates", json={"template_id": template["id"]}
    )
    assert (refused.status_code, refused.json()["detail"]) == (422, "template_switched_off")
    assert (await client.get(f"{url}/{given['id']}/file")).status_code == 200

    # Taken back from one client, deleted altogether.
    other = (await upload(client)).json()
    second = (await client.post(url, json={"template_id": other["id"]})).json()
    assert (await client.delete(f"{url}/{second['id']}")).status_code == 204
    assert (await client.delete(f"/api/templates/{template['id']}")).status_code == 204
    assert (await client.get(url)).json() == []
    assert [t["id"] for t in (await client.get("/api/templates")).json()] == [other["id"]]
    assert list(storage.MEMORY) == [f"templates/{tenant_id}/{other['id']}"]
    actions = [e["action"] for e in await audit(client)]
    assert {"contacts.template_removed", "templates.deleted", "templates.updated"} <= set(actions)


@pytest.mark.parametrize(
    ("data", "status", "code"),
    [
        (b"%PDF-1.7 a letter", 422, "template_not_docx"),
        (b"", 422, "file_empty"),
        (b"PK" + b"\0" * (5 * 1024 * 1024), 413, "template_too_large"),
    ],
)
async def test_only_word_files_are_taken(
    client: AsyncClient,
    make_user: MakeUser,
    owner_engine: AsyncEngine,
    data: bytes,
    status: int,
    code: str,
):
    await as_specialist(client, make_user, owner_engine)
    response = await upload(client, data)
    assert (response.status_code, response.json()["detail"]) == (status, code)
    assert storage.MEMORY == {}


async def test_templates_stay_in_their_workspace(
    client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine
):
    await as_specialist(client, make_user, owner_engine)
    template = (await upload(client)).json()
    anna = await new_client(client)
    given = (
        await client.post(
            f"/api/contacts/{anna['id']}/templates", json={"template_id": template["id"]}
        )
    ).json()

    await as_specialist(client, make_user, owner_engine, "vera@example.com")
    assert (await client.get("/api/templates")).json() == []
    for request in (
        client.get(f"/api/templates/{template['id']}/file"),
        client.patch(f"/api/templates/{template['id']}", json={"name": "Моё"}),
        client.delete(f"/api/templates/{template['id']}"),
        client.get(f"/api/contacts/{anna['id']}/templates"),
        client.get(f"/api/contacts/{anna['id']}/templates/{given['id']}/file"),
    ):
        assert (await request).status_code == 404
    mine = (await client.post("/api/contacts", json={"first_name": "Вера"})).json()
    stolen = await client.post(
        f"/api/contacts/{mine['id']}/templates", json={"template_id": template["id"]}
    )
    assert stolen.status_code == 404
    # Nor a case of someone else's client for one's own template.
    own = (await upload(client)).json()
    response = await client.post(
        f"/api/contacts/{mine['id']}/templates",
        json={"template_id": own["id"], "case_id": str(uuid.uuid4())},
    )
    assert response.status_code == 404


async def test_templates_need_their_module(
    client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine
):
    tenant_id = await make_user("vera@example.com")
    await login(client, "vera@example.com")
    denied = await client.get("/api/templates")
    assert (denied.status_code, denied.json()["detail"]) == (403, "module_disabled")

    await turn_on(owner_engine, tenant_id)
    template = (await upload(client)).json()
    anna = await new_client(client)
    case = await open_case(client, anna)

    # An employee with the module gives templates and downloads them; only the specialist
    # changes the list. Without «finances» the sums stay empty, as in the card.
    client.cookies.clear()
    await make_user("olga@example.com", "employee", tenant_id=tenant_id)
    async with owner_engine.begin() as conn:
        await conn.execute(
            text(
                "UPDATE users SET modules = '{clients,cases,document_templates}' "
                "WHERE email = 'olga@example.com'"
            )
        )
    await login(client, "olga@example.com")
    assert len((await client.get("/api/templates")).json()) == 1
    assert (await upload(client)).status_code == 403
    assert (
        await client.patch(f"/api/templates/{template['id']}", json={"name": "X"})
    ).status_code == 403
    url = f"/api/contacts/{anna['id']}/templates"
    given = await client.post(url, json={"template_id": template["id"], "case_id": case["id"]})
    assert given.status_code == 201
    assert (await client.get(url)).json()[0]["missing"] == ["price"]
    filled = await client.get(f"{url}/{given.json()['id']}/file")
    assert paragraphs(filled.content)[2].startswith("Стоимость: . Телефон")


@pytest.fixture
def staging_without_storage(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = get_settings().model_copy(update={"env": "staging", "s3_bucket": ""})
    monkeypatch.setattr(storage, "get_settings", lambda: settings)


async def test_without_storage_templates_cannot_be_uploaded(
    client: AsyncClient,
    make_user: MakeUser,
    owner_engine: AsyncEngine,
    staging_without_storage: None,
):
    await as_specialist(client, make_user, owner_engine)
    response = await upload(client)
    assert (response.status_code, response.json()["detail"]) == (503, "storage_unavailable")
