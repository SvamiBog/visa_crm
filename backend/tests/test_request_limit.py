"""The size of a request is limited before the server reads it into memory
(app/core/request_limit.py): small for every form, large only for a file sent by someone
signed in. A request the API turns down names the field, not what was sent (app/main.py).
Backlog S1, audit finding M7."""

import base64
import json
import logging
import re
import uuid
from collections.abc import AsyncIterator
from datetime import date

import pytest
from httpx import AsyncClient

from app import main
from app.core.documents import MAX_BYTES as MAX_DOCUMENT_BYTES
from app.core.request_limit import MAX_FORM_BYTES, MAX_UPLOAD_BYTES, takes_a_file
from app.core.workbook import MAX_BYTES as MAX_IMPORT_BYTES
from app.main import MAX_VALIDATION_ERRORS, create_app
from tests.helpers import MakeUser, login, login_first_step
from tests.test_documents import PDF, contact

LOGIN = "/api/auth/login"
SEARCH = "/api/contacts/search"
JSON = {"Content-Type": "application/json"}
KIB = 1024
MIB = 1024 * KIB
# A contact that does not exist: the limit is decided before the route looks.
NO_CONTACT = "00000000-0000-4000-8000-000000000000"


def zeros(size: int) -> bytes:
    """A JSON array of zeros `size` bytes long: cheap to send, dear to decode."""
    return b"[" + b"0," * ((size - 3) // 2) + b"0]"


def pdf(size: int) -> bytes:
    return PDF + b"\0" * (size - len(PDF))


def upload(data: bytes) -> bytes:
    """The body of a document upload: the file inside the JSON as base64."""
    content = base64.b64encode(data).decode()
    return json.dumps({"kind": "contract", "file_name": "c.pdf", "content": content}).encode()


def test_the_largest_file_the_crm_takes_fits_into_an_upload():
    # A file goes in the JSON body as base64: four characters for every three bytes.
    largest = max(MAX_DOCUMENT_BYTES, MAX_IMPORT_BYTES)
    assert largest * 4 / 3 + 64 * KIB < MAX_UPLOAD_BYTES


def _takes_a_file(schema: dict, components: dict) -> bool:
    if "$ref" in schema:
        schema = components[schema["$ref"].rsplit("/", 1)[-1]]
    return any(
        field.get("format") == "base64" or field.get("contentEncoding") == "base64"
        for field in schema.get("properties", {}).values()
    )


def test_only_the_routes_that_take_a_file_get_the_upload_limit():
    spec = create_app().openapi()
    components = spec["components"]["schemas"]
    uploads = []
    for path, operations in spec["paths"].items():
        # A portal link's token has its own shape (app/core/portal.py).
        concrete = path.replace("{token}", "t" * 43)
        concrete = re.sub(r"\{[^}]+\}", str(uuid.uuid4()), concrete)
        for method, operation in operations.items():
            content = operation.get("requestBody", {}).get("content", {})
            schema = next(iter(content.values()))["schema"] if content else {}
            expected = _takes_a_file(schema, components)
            assert takes_a_file(method.upper(), concrete) == expected, (method, path)
            if expected:
                uploads.append(f"{method.upper()} {path}")
    assert sorted(uploads) == [
        "POST /api/cases/{case_id}/package/{item_id}/files",
        "POST /api/contacts/{contact_id}/documents",
        "POST /api/imports/uploads",
        "POST /api/portal/{token}/items/{item_id}/files",
        "POST /api/templates",
        "PUT /api/templates/{template_id}/file",
    ]


@pytest.mark.parametrize("path", [LOGIN, SEARCH])
async def test_an_anonymous_form_over_the_limit_is_refused_unread(client: AsyncClient, path):
    """Nobody is logged in: the body is refused by its declared size, not decoded."""
    response = await client.post(path, content=zeros(300 * KIB), headers=JSON)
    assert response.status_code == 413
    assert response.json() == {"detail": "request_too_large"}
    assert response.headers["x-content-type-options"] == "nosniff"


async def test_a_signed_in_form_over_the_limit_is_refused(client: AsyncClient, make_user: MakeUser):
    await make_user("anna@example.com")
    await login(client, "anna@example.com")
    response = await client.post(SEARCH, content=zeros(MAX_FORM_BYTES + 1), headers=JSON)
    assert (response.status_code, response.json()) == (413, {"detail": "request_too_large"})


async def test_the_largest_form_fits(client: AsyncClient, make_user: MakeUser):
    """The questionnaire is the largest form: every list full, every text at its longest,
    four bytes a character."""
    await make_user("anna@example.com")
    await login(client, "anna@example.com")
    anna = await contact(client)
    wide = "😀"
    answers = {
        "passport_number": "A" * 20,
        "passport_issued_on": date(2020, 1, 1).isoformat(),
        "passport_expires_on": date(2030, 1, 1).isoformat(),
        "passport_issued_by": wide * 200,
        "nationality": "BY",
        "sex": "F",
        "personal_number": "B" * 20,
        "birth_place": wide * 200,
        "visas": [
            {
                "kind": "schengen",
                "country": "DE",
                "purpose": "tourism",
                "valid_from": "2024-01-01",
                "valid_to": "2024-12-31",
                "note": wide * 500,
            }
        ]
        * 50,
        "relatives_abroad": ["DE"] * 50,
        "refusals": [{"country": "DE", "year": 2020, "reason": wide * 500}] * 50,
        "deportation": True,
        "deportation_note": wide * 500,
        "humanitarian_trips": ["IT"] * 10,
        "note": wide * 2000,
    }
    body = json.dumps(answers, ensure_ascii=False).encode()
    assert 200 * KIB < len(body) < MAX_FORM_BYTES
    response = await client.put(f"/api/contacts/{anna}/questionnaire", content=body, headers=JSON)
    assert response.status_code == 200, response.text


async def pieces(total: int) -> AsyncIterator[bytes]:
    chunk = 64 * KIB
    for _ in range(total // chunk + 1):
        yield b"x" * chunk


@pytest.mark.parametrize("path", [LOGIN, f"/api/contacts/{NO_CONTACT}/documents"])
async def test_a_body_sent_in_pieces_without_its_size_is_counted(client: AsyncClient, path):
    response = await client.post(path, content=pieces(MAX_FORM_BYTES + 1), headers=JSON)
    assert response.status_code == 413
    assert response.json() == {"detail": "request_too_large"}


async def test_a_file_without_a_live_session_gets_the_form_limit(
    client: AsyncClient, make_user: MakeUser
):
    """A cookie alone proves nothing: made up, or a login still waiting for its code."""
    url = f"/api/contacts/{NO_CONTACT}/documents"
    body = upload(pdf(15 * MIB * 3 // 4))
    assert MAX_FORM_BYTES < len(body) < MAX_UPLOAD_BYTES

    anonymous = await client.post(url, content=body, headers=JSON)
    assert (anonymous.status_code, anonymous.json()) == (413, {"detail": "request_too_large"})

    client.cookies.set("visacrm_session", "made-up-session-token")
    made_up = await client.post(url, content=body, headers=JSON)
    assert (made_up.status_code, made_up.json()) == (413, {"detail": "request_too_large"})

    client.cookies.clear()
    await make_user("anna@example.com")  # with two-factor login
    first_step = await login_first_step(client, "anna@example.com")
    assert first_step.json()["mfa_required"] is True
    pending = await client.post(url, content=body, headers=JSON)
    assert (pending.status_code, pending.json()) == (413, {"detail": "request_too_large"})


async def test_a_failed_session_lookup_is_an_unexpected_error(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
):
    """The log gets the error's type, not its text (app/core/server_errors.py)."""

    async def broken(request: object) -> None:
        raise RuntimeError("Zzsecretsurname")

    monkeypatch.setattr(main, "current_user_any", broken)
    # Alembic's logging config switches off the loggers made before the migrations ran.
    monkeypatch.setattr(logging.getLogger("app.core.server_errors"), "disabled", False)
    with caplog.at_level(logging.ERROR):
        response = await client.post(
            f"/api/contacts/{NO_CONTACT}/documents", content=b"{}", headers=JSON
        )
    assert response.status_code == 500
    assert "failed: RuntimeError" in caplog.text
    assert "Zzsecretsurname" not in caplog.text


async def test_a_signed_in_specialist_uploads_the_largest_document(
    client: AsyncClient, make_user: MakeUser
):
    await make_user("anna@example.com")
    await login(client, "anna@example.com")
    anna = await contact(client)
    body = upload(pdf(MAX_DOCUMENT_BYTES))
    assert MAX_FORM_BYTES < len(body) < MAX_UPLOAD_BYTES
    response = await client.post(f"/api/contacts/{anna}/documents", content=body, headers=JSON)
    assert response.status_code == 201, response.text


async def test_a_request_within_the_limit_is_read_as_before(client: AsyncClient):
    response = await client.post(LOGIN, json={"email": "nobody@example.com", "password": "x"})
    assert response.status_code == 401
    assert response.json()["detail"] == "invalid_credentials"


@pytest.mark.parametrize("path", [LOGIN, "/api/auth/login/verify"])
async def test_a_rejected_body_is_not_sent_back(client: AsyncClient, path):
    response = await client.post(path, json=[1, 2, 3])
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail
    assert all(set(item) == {"type", "loc", "msg"} for item in detail)


async def test_a_rejected_field_is_named_without_its_value(client: AsyncClient):
    """The forms put the message under the field the API names (components/Field.tsx)."""
    email = "secret-" * 60 + "@example.com"
    response = await client.post(LOGIN, json={"email": email, "password": "x"})
    assert response.status_code == 422
    assert [item["loc"] for item in response.json()["detail"]] == [["body", "email"]]
    assert "secret-" not in response.text


async def test_a_long_list_of_bad_items_is_answered_in_short(
    client: AsyncClient, make_user: MakeUser
):
    await make_user("anna@example.com")
    await login(client, "anna@example.com")
    response = await client.put("/api/dictionaries/prepayments", json=[0] * 5000)
    assert response.status_code == 422
    assert len(response.json()["detail"]) == MAX_VALIDATION_ERRORS
