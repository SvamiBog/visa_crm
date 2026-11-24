"""The client portal through the API (backlog 2.9): the link and the code in the case, the
client's sign-in by code, the data notice, uploads to checklist items, the specialist's
review, access of other workspaces and employees, and the audit log."""

import asyncio
import base64
import re
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.core import mail, portal
from app.core.config import get_settings
from app.core.db import scope_to_tenant
from app.core.storage import MEMORY
from app.models import AuditEvent, ClientLink, PackageFile, PortalConsent
from tests.helpers import MakeUser, give_cases
from tests.test_package import as_specialist, countries, open_case, own, package, relogin

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
PDF = b"%PDF-1.7\n" + b"x" * 64
CONTROLLER = {
    "name": "Anna Kowalska JDG",
    "address": "ul. Prosta 1, 00-001 Warszawa",
    "email": "anna@example.com",
}


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


@pytest.fixture
async def visitor(client: AsyncClient) -> AsyncIterator[AsyncClient]:
    """The client's own browser: the same app, its own cookies."""
    transport = ASGITransport(app=client._transport.app)  # type: ignore[attr-defined]
    async with AsyncClient(transport=transport, base_url="http://testserver") as http:
        yield http


async def setup_case(
    client: AsyncClient,
    make_user: MakeUser,
    owner_engine: AsyncEngine,
    email: str = "anna@example.com",
    *,
    contact_email: str | None = "client@example.com",
    controller: bool = True,
) -> tuple[dict, dict, uuid.UUID | None]:
    """A specialist with the package module, a checklist item for Poland and a case."""
    tenant_id = await as_specialist(client, make_user, owner_engine, email)
    if controller:
        saved = await client.put("/api/workspace/controller", json=CONTROLLER)
        assert saved.status_code == 200, saved.text
    codes = await countries(client)
    item = await own(client, codes["PL"], name="Выписка из банка", note="За 3 месяца")
    case = await open_case(client)
    if contact_email:
        updated = await client.patch(
            f"/api/contacts/{case['contact_id']}", json={"email": contact_email}
        )
        assert updated.status_code == 200, updated.text
    return case, item, tenant_id


async def portal_of(client: AsyncClient, case: dict) -> dict:
    response = await client.get(f"/api/cases/{case['id']}/portal")
    assert response.status_code == 200, response.text
    return response.json()


async def staff_code(client: AsyncClient, case: dict) -> str:
    response = await client.post(
        f"/api/cases/{case['id']}/portal/code", json={"channel": "specialist"}
    )
    assert response.status_code == 200, response.text
    return response.json()["code"]


async def signed_in(client: AsyncClient, visitor: AsyncClient, case: dict) -> str:
    token = (await portal_of(client, case))["token"]
    code = await staff_code(client, case)
    response = await visitor.post(f"/api/portal/{token}/login", json={"code": code})
    assert response.status_code == 200, response.text
    return token


async def consented(visitor: AsyncClient, token: str) -> dict:
    page = (await visitor.get(f"/api/portal/{token}")).json()
    response = await visitor.post(
        f"/api/portal/{token}/consent",
        json={
            "notice_id": page["consent"]["notice"]["id"],
            "special_categories": True,
            "given_by": "applicant",
        },
    )
    assert response.status_code == 201, response.text
    return page


async def upload(visitor: AsyncClient, token: str, item: dict, data: bytes = PNG, name="scan.png"):
    return await visitor.post(
        f"/api/portal/{token}/items/{item['id']}/files",
        json={"file_name": name, "content": b64(data)},
    )


async def actions(owner_engine: AsyncEngine, prefix: str = "portal.") -> list[AuditEvent]:
    async with owner_engine.connect() as conn:
        rows = await conn.execute(
            text(
                "SELECT action, actor_id, target_id, details FROM audit_events "
                "WHERE action LIKE :p ORDER BY occurred_at, id"
            ),
            {"p": prefix + "%"},
        )
        return list(rows)


# --- The link in the case ------------------------------------------------------------


async def test_every_open_case_has_a_link_and_the_database_keeps_only_its_hash(
    client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine, sessionmaker
):
    case, _, tenant_id = await setup_case(client, make_user, owner_engine)
    first = await portal_of(client, case)
    assert first["open"] is True and portal.is_link_token(first["token"])
    # The same link each time: it is made once and copied again.
    assert (await portal_of(client, case))["token"] == first["token"]
    async with owner_engine.connect() as conn:
        dump = (await conn.execute(text("SELECT * FROM client_links"))).all()
    assert len(dump) == 1
    assert first["token"].encode() not in b"".join(
        v if isinstance(v, bytes) else str(v).encode() for v in dump[0]
    )
    assert [a.action for a in await actions(owner_engine)] == ["portal.link_issued"]


async def test_a_link_asked_for_at_once_from_two_tabs_is_one_link(
    client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine
):
    case, _, _ = await setup_case(client, make_user, owner_engine)
    answers = await asyncio.gather(
        *(client.get(f"/api/cases/{case['id']}/portal") for _ in range(5))
    )
    assert {r.status_code for r in answers} == {200}
    assert len({r.json()["token"] for r in answers}) == 1


async def test_revoking_gives_a_new_link_and_the_old_one_and_its_sessions_stop(
    client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine, visitor: AsyncClient
):
    case, _, _ = await setup_case(client, make_user, owner_engine)
    old = await signed_in(client, visitor, case)
    assert (await visitor.get(f"/api/portal/{old}")).status_code == 200

    revoked = await client.post(f"/api/cases/{case['id']}/portal/revoke")
    assert revoked.status_code == 200, revoked.text
    new = revoked.json()["token"]
    assert new != old
    assert (await visitor.get(f"/api/portal/{old}")).status_code == 404
    assert (await visitor.post(f"/api/portal/{old}/code")).json()["detail"] == "link_invalid"
    assert (await visitor.get(f"/api/portal/{new}")).status_code == 401
    assert "portal.link_revoked" in [a.action for a in await actions(owner_engine)]


@pytest.mark.parametrize("stage", ["review", "result"])
async def test_a_case_handed_over_for_review_loses_its_link_and_code(
    client: AsyncClient,
    make_user: MakeUser,
    owner_engine: AsyncEngine,
    visitor: AsyncClient,
    stage: str,
):
    case, _, _ = await setup_case(client, make_user, owner_engine)
    token = await signed_in(client, visitor, case)
    async with owner_engine.begin() as conn:
        await conn.execute(
            text("UPDATE visa_cases SET stage = CAST(:s AS case_stage) WHERE id = :id"),
            {"id": case["id"], "s": stage},
        )
    assert (await visitor.get(f"/api/portal/{token}")).status_code == 404
    state = await portal_of(client, case)
    assert state["open"] is False and state["token"] is None and state["code"] is None
    # The link and the code are gone from the database too, not only refused.
    async with owner_engine.connect() as conn:
        live = await conn.execute(
            text(
                "SELECT count(*) FROM client_links "
                "WHERE revoked_at IS NULL OR code_hash IS NOT NULL"
            )
        )
        assert live.scalar() == 0
    refused = await client.post(f"/api/cases/{case['id']}/portal/code", json={"channel": "email"})
    assert refused.status_code == 409 and refused.json()["detail"] == "portal_closed"


# --- The code ------------------------------------------------------------------------


async def test_the_code_stays_valid_and_visible_until_a_new_one_replaces_it(
    client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine, visitor: AsyncClient
):
    case, _, _ = await setup_case(client, make_user, owner_engine)
    token = (await portal_of(client, case))["token"]
    code = await staff_code(client, case)
    assert portal.is_code(code)
    assert (await visitor.get(f"/api/portal/{token}")).status_code == 401

    response = await visitor.post(f"/api/portal/{token}/login", json={"code": code})
    assert response.status_code == 200
    assert (await visitor.get(f"/api/portal/{token}")).status_code == 200
    # No expiry: weeks later, from another device, the same code still opens the page,
    # and the specialist still sees it in the case.
    async with owner_engine.begin() as conn:
        await conn.execute(
            text("UPDATE client_links SET code_sent_at = now() - interval '40 days'")
        )
    other = AsyncClient(transport=visitor._transport, base_url="http://testserver")  # type: ignore[attr-defined]
    async with other:
        assert (
            await other.post(f"/api/portal/{token}/login", json={"code": code})
        ).status_code == 200
    assert (await portal_of(client, case))["code"] == code
    # «Новый код» replaces it: the old one stops working.
    newer = await staff_code(client, case)
    if newer != code:
        refused = await visitor.post(f"/api/portal/{token}/login", json={"code": code})
        assert refused.status_code == 400 and refused.json()["detail"] == "code_invalid"
    assert (await portal_of(client, case))["code"] == newer
    logged = {a.action: a.details for a in await actions(owner_engine)}
    link = logged["portal.link_issued"]["link"]
    assert logged["portal.code_issued"] == {
        "contact": case["contact_id"],
        "link": link,
        "channel": "specialist",
        "new": "true",
    }
    # The client has no account: the event names the link and how the code came.
    assert logged["portal.signed_in"]["link"] == link


async def test_the_client_asks_for_a_code_and_it_goes_to_the_email_of_the_card(
    client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine, visitor: AsyncClient
):
    mail.OUTBOX.clear()
    case, _, _ = await setup_case(client, make_user, owner_engine)
    token = (await portal_of(client, case))["token"]
    asked = await visitor.post(f"/api/portal/{token}/code")
    assert asked.status_code == 200 and asked.json() == {"channel": "email"}
    assert len(mail.OUTBOX) == 1 and mail.OUTBOX[0].to == "client@example.com"
    code = re.search(r"\b(\d{6})\b", mail.OUTBOX[0].text).group(1)
    # The letter carries no link: a forwarded letter does not open the page on its own.
    assert token not in mail.OUTBOX[0].text
    signed = await visitor.post(f"/api/portal/{token}/login", json={"code": code})
    assert signed.status_code == 200
    # It is the link's code, the one the specialist sees.
    assert (await portal_of(client, case))["code"] == code
    # Asking again within a minute is refused.
    soon = await visitor.post(f"/api/portal/{token}/code")
    assert soon.status_code == 429 and soon.json()["detail"] == "code_sent_recently"
    # Asking later sends the same code again: a new one would lock out the one the
    # specialist passed on.
    async with owner_engine.begin() as conn:
        await conn.execute(text("UPDATE client_links SET code_sent_at = now() - interval '2 min'"))
    assert (await visitor.post(f"/api/portal/{token}/code")).status_code == 200
    assert code in mail.OUTBOX[-1].text and len(mail.OUTBOX) == 2


async def test_without_an_email_the_client_is_told_to_ask_the_specialist(
    client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine, visitor: AsyncClient
):
    mail.OUTBOX.clear()
    case, _, _ = await setup_case(client, make_user, owner_engine, contact_email=None)
    token = (await portal_of(client, case))["token"]
    asked = await visitor.post(f"/api/portal/{token}/code")
    assert asked.status_code == 200 and asked.json() == {"channel": "specialist"}
    assert mail.OUTBOX == []
    refused = await client.post(f"/api/cases/{case['id']}/portal/code", json={"channel": "email"})
    assert refused.status_code == 422 and refused.json()["detail"] == "no_email"


async def test_wrong_and_replaced_codes_get_the_same_answer(
    client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine, visitor: AsyncClient
):
    case, _, _ = await setup_case(client, make_user, owner_engine)
    token = (await portal_of(client, case))["token"]
    first = await staff_code(client, case)
    async with owner_engine.begin() as conn:
        await conn.execute(text("UPDATE client_links SET code_sent_at = now() - interval '2 min'"))
    second = await staff_code(client, case)
    wrong = "000000" if second != "000000" else "111111"
    answers = []
    for code in (wrong, first if first != second else wrong, "12345"):
        r = await visitor.post(f"/api/portal/{token}/login", json={"code": code})
        answers.append((r.status_code, r.json()["detail"]))
    assert set(answers) == {(400, "code_invalid")}


async def test_after_five_tries_even_the_right_code_is_refused(
    client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine, visitor: AsyncClient
):
    case, _, _ = await setup_case(client, make_user, owner_engine)
    token = (await portal_of(client, case))["token"]
    code = await staff_code(client, case)
    wrong = "000000" if code != "000000" else "111111"
    for _ in range(portal.MAX_CODE_ATTEMPTS):
        r = await visitor.post(f"/api/portal/{token}/login", json={"code": wrong})
        assert r.json()["detail"] == "code_invalid"
    r = await visitor.post(f"/api/portal/{token}/login", json={"code": code})
    assert r.status_code == 400 and r.json()["detail"] == "code_invalid"
    # The specialist sees the code is blocked; a new one opens the page again.
    assert (await portal_of(client, case))["code_blocked"] is True
    async with owner_engine.begin() as conn:
        await conn.execute(text("UPDATE client_links SET code_sent_at = now() - interval '2 min'"))
    fresh = await staff_code(client, case)
    assert (await portal_of(client, case))["code_blocked"] is False
    r = await visitor.post(f"/api/portal/{token}/login", json={"code": fresh})
    assert r.status_code == 200


async def test_a_right_code_forgives_the_wrong_tries_before_it(
    client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine, visitor: AsyncClient
):
    case, _, _ = await setup_case(client, make_user, owner_engine)
    token = (await portal_of(client, case))["token"]
    code = await staff_code(client, case)
    wrong = "000000" if code != "000000" else "111111"
    for _ in range(2):
        for _ in range(portal.MAX_CODE_ATTEMPTS - 1):
            await visitor.post(f"/api/portal/{token}/login", json={"code": wrong})
        r = await visitor.post(f"/api/portal/{token}/login", json={"code": code})
        assert r.status_code == 200


async def test_ten_codes_a_day_at_most(
    client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine
):
    case, _, _ = await setup_case(client, make_user, owner_engine)
    await portal_of(client, case)
    for _ in range(portal.MAX_CODES_A_DAY):
        async with owner_engine.begin() as conn:
            await conn.execute(
                text("UPDATE client_links SET code_sent_at = now() - interval '2 min'")
            )
        await staff_code(client, case)
    async with owner_engine.begin() as conn:
        await conn.execute(text("UPDATE client_links SET code_sent_at = now() - interval '2 min'"))
    r = await client.post(f"/api/cases/{case['id']}/portal/code", json={"channel": "specialist"})
    assert r.status_code == 429 and r.json()["detail"] == "too_many_codes"


async def test_too_many_failures_from_one_address_are_paused(
    client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine, visitor: AsyncClient
):
    case, _, _ = await setup_case(client, make_user, owner_engine)
    await portal_of(client, case)
    made_up = portal.new_link_token()
    answers = [
        (await visitor.post(f"/api/portal/{made_up}/login", json={"code": "123456"})).status_code
        for _ in range(25)
    ]
    assert answers[0] == 404 and answers[-1] == 429


# --- The client's page ---------------------------------------------------------------


async def test_the_client_sees_the_country_items_and_requirements_but_no_scan(
    client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine, visitor: AsyncClient
):
    case, item, _ = await setup_case(client, make_user, owner_engine)
    token = await signed_in(client, visitor, case)
    page = await consented(visitor, token)
    assert page["country"]
    assert page["consent"]["needed"] is True
    assert "Anna Kowalska JDG" in page["consent"]["notice"]["body"]
    assert page["consent"]["withdraw_email"] == "anna@example.com"
    [shown] = [i for i in page["items"] if i["id"] == item["id"]]
    assert shown["name"] == "Выписка из банка" and shown["note"] == "За 3 месяца"
    assert shown["status"] == "to_upload" and shown["files"] == []

    assert (await upload(visitor, token, item)).status_code == 201
    page = (await visitor.get(f"/api/portal/{token}")).json()
    [shown] = [i for i in page["items"] if i["id"] == item["id"]]
    assert shown["status"] == "pending"
    assert [f["file_name"] for f in shown["files"]] == ["scan.png"]
    assert page["consent"]["needed"] is False
    # No route of the portal gives a file back.
    file_id = shown["files"][0]["id"]
    assert (await visitor.get(f"/api/portal/{token}/files/{file_id}")).status_code in (404, 405)


async def test_uploads_wait_for_the_consent_and_for_the_controller_details(
    client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine, visitor: AsyncClient
):
    case, item, _ = await setup_case(client, make_user, owner_engine, controller=False)
    token = await signed_in(client, visitor, case)
    page = (await visitor.get(f"/api/portal/{token}")).json()
    assert page["ready"] is False and page["consent"]["notice"] is None
    refused = await upload(visitor, token, item)
    assert refused.status_code == 409 and refused.json()["detail"] == "portal_not_ready"

    await client.put("/api/workspace/controller", json=CONTROLLER)
    refused = await upload(visitor, token, item)
    assert refused.status_code == 409 and refused.json()["detail"] == "consent_required"


async def test_the_consent_is_kept_as_shown_and_never_changed(
    client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine, visitor: AsyncClient
):
    case, _, _ = await setup_case(client, make_user, owner_engine)
    token = await signed_in(client, visitor, case)
    page = (await visitor.get(f"/api/portal/{token}")).json()
    representative = await visitor.post(
        f"/api/portal/{token}/consent",
        json={
            "notice_id": page["consent"]["notice"]["id"],
            "special_categories": False,
            "given_by": "representative",
        },
    )
    assert representative.status_code == 422  # a representative names themself
    given = await visitor.post(
        f"/api/portal/{token}/consent",
        json={
            "notice_id": page["consent"]["notice"]["id"],
            "special_categories": False,
            "given_by": "representative",
            "representative_name": "Мама",
        },
        headers={"User-Agent": "TestBrowser/1"},
    )
    assert given.status_code == 201
    async with owner_engine.connect() as conn:
        row = (await conn.execute(text("SELECT * FROM portal_consents"))).mappings().one()
        assert row["code_channel"] == "specialist" and row["user_agent"] == "TestBrowser/1"
        assert row["special_categories"] is False and row["representative_name"] == "Мама"
    with pytest.raises(Exception, match="never changed"):
        async with owner_engine.begin() as conn:
            await conn.execute(text("UPDATE portal_consents SET special_categories = true"))
    card = await portal_of(client, case)
    assert card["consent"]["special_categories"] is False
    assert card["consent"]["given_by"] == "representative"
    assert "portal.consent_given" in [a.action for a in await actions(owner_engine)]


async def test_without_the_special_consent_ordinary_documents_still_go_up(
    client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine, visitor: AsyncClient
):
    # The documents are processed under the contract (GDPR Art. 6(1)(b)); the consent to
    # special data is optional and only covers health data inside them.
    case, item, _ = await setup_case(client, make_user, owner_engine)
    token = await signed_in(client, visitor, case)
    page = (await visitor.get(f"/api/portal/{token}")).json()
    body = {
        "notice_id": page["consent"]["notice"]["id"],
        "special_categories": False,
        "given_by": "applicant",
    }
    for _ in range(2):  # a second press adds nothing
        assert (await visitor.post(f"/api/portal/{token}/consent", json=body)).status_code == 201
    async with owner_engine.connect() as conn:
        assert (await conn.execute(text("SELECT count(*) FROM portal_consents"))).scalar() == 1
    assert (await upload(visitor, token, item)).status_code == 201


async def test_the_withdrawal_email_cannot_carry_more_than_an_address(
    client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine
):
    await setup_case(client, make_user, owner_engine)
    tricked = CONTROLLER | {"email": "anna@example.com?bcc=x@example.com"}
    assert (await client.put("/api/workspace/controller", json=tricked)).status_code == 422


async def test_files_are_checked_by_content_up_to_20_mb(
    client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine, visitor: AsyncClient
):
    case, item, _ = await setup_case(client, make_user, owner_engine)
    token = await signed_in(client, visitor, case)
    await consented(visitor, token)
    fake = await upload(visitor, token, item, b"MZ\x90\x00 not a scan", name="scan.pdf")
    assert fake.status_code == 422 and fake.json()["detail"] == "document_type_not_allowed"
    big = PDF + b"0" * (portal.MAX_FILE_BYTES - len(PDF) + 1)
    too_big = await upload(visitor, token, item, big, name="big.pdf")
    assert too_big.status_code == 413
    just = PDF + b"0" * (portal.MAX_FILE_BYTES - len(PDF))
    assert (await upload(visitor, token, item, just, name="ok.pdf")).status_code == 201
    stored = [blob for key, blob in MEMORY.items() if key.startswith("documents/")]
    assert len(stored) == 1 and b"%PDF" not in stored[0][:64]  # encrypted


async def test_an_item_of_another_case_or_workspace_takes_no_upload(
    client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine, visitor: AsyncClient
):
    case, item, _ = await setup_case(client, make_user, owner_engine)
    token = await signed_in(client, visitor, case)
    await consented(visitor, token)
    r = await upload(visitor, token, {"id": str(uuid.uuid4())})
    assert r.status_code == 404


async def test_the_client_deletes_only_their_own_file_still_waiting(
    client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine, visitor: AsyncClient
):
    case, item, _ = await setup_case(client, make_user, owner_engine)
    token = await signed_in(client, visitor, case)
    await consented(visitor, token)
    first = (await upload(visitor, token, item)).json()
    second = (await upload(visitor, token, item)).json()
    gone = await visitor.delete(f"/api/portal/{token}/files/{first['id']}")
    assert gone.status_code == 204
    accepted = await client.post(
        f"/api/cases/{case['id']}/package-files/{second['id']}/accept", json={}
    )
    assert accepted.status_code == 200
    kept = await visitor.delete(f"/api/portal/{token}/files/{second['id']}")
    assert kept.status_code == 409 and kept.json()["detail"] == "file_reviewed"


async def test_the_crm_cookie_does_not_open_the_portal_and_the_portal_cookie_not_the_crm(
    client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine, visitor: AsyncClient
):
    case, _, _ = await setup_case(client, make_user, owner_engine)
    token = (await portal_of(client, case))["token"]
    assert (await client.get(f"/api/portal/{token}")).status_code == 401
    await signed_in(client, visitor, case)
    assert (await visitor.get("/api/auth/me")).status_code == 401
    assert (await visitor.get(f"/api/cases/{case['id']}/portal")).status_code == 401


async def test_a_session_of_one_link_does_not_open_another_case(
    client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine, visitor: AsyncClient
):
    case, _, _ = await setup_case(client, make_user, owner_engine)
    other = await open_case(client)
    await signed_in(client, visitor, case)
    other_token = (await portal_of(client, other))["token"]
    assert (await visitor.get(f"/api/portal/{other_token}")).status_code == 401


async def test_a_session_ends_after_an_hour_without_actions(
    client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine, visitor: AsyncClient
):
    case, _, _ = await setup_case(client, make_user, owner_engine)
    token = await signed_in(client, visitor, case)
    async with owner_engine.begin() as conn:
        await conn.execute(
            text("UPDATE client_sessions SET last_seen_at = now() - interval '61 min'")
        )
    assert (await visitor.get(f"/api/portal/{token}")).status_code == 401


async def test_public_addresses_are_not_indexed_and_send_no_referrer(
    client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine, visitor: AsyncClient
):
    made_up = portal.new_link_token()
    for response in (
        await visitor.post(f"/api/portal/{made_up}/code"),
        await visitor.get(f"/u/{made_up}"),
    ):
        assert response.headers["Referrer-Policy"] == "no-referrer"
        assert "noindex" in response.headers["X-Robots-Tag"]


async def test_a_large_anonymous_upload_is_refused_before_it_is_read(
    client: AsyncClient, visitor: AsyncClient, owner_engine: AsyncEngine
):
    made_up = portal.new_link_token()
    body = b"{" + b" " * (300 * 1024) + b"}"
    r = await visitor.post(
        f"/api/portal/{made_up}/items/{uuid.uuid4()}/files",
        content=body,
        headers={"Content-Type": "application/json"},
    )
    assert r.status_code == 413 and r.json()["detail"] == "request_too_large"


# --- The specialist's review ---------------------------------------------------------


async def test_accepting_marks_the_item_present_with_the_document_date(
    client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine, visitor: AsyncClient
):
    case, item, _ = await setup_case(client, make_user, owner_engine)
    token = await signed_in(client, visitor, case)
    await consented(visitor, token)
    uploaded = (await upload(visitor, token, item)).json()

    listed = await package(client, case)
    [row] = [i for i in listed["items"] if i["item_id"] == item["id"]]
    assert [f["status"] for f in row["files"]] == ["pending"]
    assert row["files"][0]["source"] == "client"

    day = datetime.now(UTC).date().isoformat()
    r = await client.post(
        f"/api/cases/{case['id']}/package-files/{uploaded['id']}/accept",
        json={"document_date": day},
    )
    assert r.status_code == 200 and r.json()["status"] == "accepted"
    [row] = [i for i in (await package(client, case))["items"] if i["item_id"] == item["id"]]
    assert row["state"] == "present" and row["document_date"] == day
    page = (await visitor.get(f"/api/portal/{token}")).json()
    assert [i["status"] for i in page["items"] if i["id"] == item["id"]] == ["accepted"]


async def test_returning_needs_a_reason_and_the_client_sees_it(
    client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine, visitor: AsyncClient
):
    case, item, _ = await setup_case(client, make_user, owner_engine)
    token = await signed_in(client, visitor, case)
    await consented(visitor, token)
    uploaded = (await upload(visitor, token, item)).json()
    url = f"/api/cases/{case['id']}/package-files/{uploaded['id']}/return"
    assert (await client.post(url, json={"reason": "other"})).status_code == 422
    r = await client.post(url, json={"reason": "other", "note": "Не видно печати"})
    assert r.status_code == 200 and r.json()["status"] == "returned"
    page = (await visitor.get(f"/api/portal/{token}")).json()
    [shown] = [i for i in page["items"] if i["id"] == item["id"]]
    assert shown["status"] == "returned"
    assert shown["files"][0]["return_reason"] == "other"
    assert shown["files"][0]["return_note"] == "Не видно печати"
    assert [a.action for a in await actions(owner_engine, "portal.file_")] == [
        "portal.file_uploaded",
        "portal.file_returned",
    ]


async def test_the_specialists_own_upload_is_accepted_at_once(
    client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine
):
    case, item, _ = await setup_case(client, make_user, owner_engine)
    r = await client.post(
        f"/api/cases/{case['id']}/package/{item['id']}/files",
        json={"file_name": "выписка.pdf", "content": b64(PDF), "document_date": None},
    )
    assert r.status_code == 201, r.text
    assert r.json()["status"] == "accepted" and r.json()["source"] == "staff"
    [row] = [i for i in (await package(client, case))["items"] if i["item_id"] == item["id"]]
    assert row["state"] == "present"
    download = await client.get(f"/api/cases/{case['id']}/package-files/{r.json()['id']}")
    assert download.status_code == 200 and download.content == PDF
    assert download.headers["Content-Disposition"].startswith("attachment")
    logged = [(a.action, a.details) for a in await actions(owner_engine, "portal.file_")]
    assert [a for a, _ in logged] == ["portal.file_uploaded", "portal.file_opened"]
    # Ids only: the name of the file may carry the client's name.
    assert all("выписка" not in str(d) for _, d in logged)


# --- Other workspaces and employees -------------------------------------------------


async def test_another_specialist_sees_nothing_of_the_portal(
    client: AsyncClient,
    make_user: MakeUser,
    owner_engine: AsyncEngine,
    visitor: AsyncClient,
    sessionmaker: async_sessionmaker[AsyncSession],
):
    case, item, tenant_id = await setup_case(client, make_user, owner_engine)
    token = await signed_in(client, visitor, case)
    await consented(visitor, token)
    uploaded = (await upload(visitor, token, item)).json()

    other_tenant = await as_specialist(client, make_user, owner_engine, "boris@example.com")
    base = f"/api/cases/{case['id']}"
    assert (await client.get(f"{base}/portal")).status_code == 404
    assert (await client.post(f"{base}/portal/revoke")).status_code == 404
    assert (
        await client.post(f"{base}/portal/code", json={"channel": "specialist"})
    ).status_code == 404
    assert (
        await client.get(f"/api/cases/{case['id']}/package-files/{uploaded['id']}")
    ).status_code == 404
    assert (
        await client.post(f"/api/cases/{case['id']}/package-files/{uploaded['id']}/accept", json={})
    ).status_code == 404
    async with sessionmaker() as session:
        scope_to_tenant(session, other_tenant)
        assert (await session.scalars(select(ClientLink))).all() == []
        assert (await session.scalars(select(PackageFile))).all() == []
        assert (await session.scalars(select(PortalConsent))).all() == []
    async with sessionmaker() as session:
        scope_to_tenant(session, tenant_id)
        assert len((await session.scalars(select(PackageFile))).all()) == 1


async def test_an_employee_works_with_the_portal_of_given_cases_only(
    client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine
):
    case, item, tenant_id = await setup_case(client, make_user, owner_engine)
    other = await open_case(client)
    staff_upload = await client.post(
        f"/api/cases/{case['id']}/package/{item['id']}/files",
        json={"file_name": "a.pdf", "content": b64(PDF)},
    )
    file_id = staff_upload.json()["id"]
    await make_user("staff@example.com", "employee", tenant_id=tenant_id)
    async with owner_engine.begin() as conn:
        await conn.execute(
            text(
                "UPDATE users SET modules = (SELECT modules FROM tenants WHERE id = :t), "
                "daily_card_limit = 1 WHERE email = 'staff@example.com'"
            ),
            {"t": tenant_id},
        )
    await give_cases(owner_engine, "staff@example.com", case["id"])
    await relogin(client, "staff@example.com")

    assert (await client.get(f"/api/cases/{other['id']}/portal")).status_code == 404
    assert (await client.get(f"/api/cases/{case['id']}/portal")).status_code == 200
    download = await client.get(f"/api/cases/{case['id']}/package-files/{file_id}")
    assert download.status_code == 200
    # The daily limit counts the client card the file belongs to (one card a day here).
    await relogin(client, "anna@example.com")
    third = await open_case(client)
    await give_cases(owner_engine, "staff@example.com", third["id"])
    await relogin(client, "staff@example.com")
    over = await client.get(f"/api/cases/{third['id']}/portal")
    assert over.status_code == 429


async def test_controller_details_are_the_specialists_own(
    client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine
):
    _, _, tenant_id = await setup_case(client, make_user, owner_engine)
    saved = (await client.get("/api/workspace/controller")).json()
    assert saved["name"] == CONTROLLER["name"]
    assert "UODO" in saved["supervisory_authority_default"]
    await make_user("staff@example.com", "employee", tenant_id=tenant_id)
    await relogin(client, "staff@example.com")
    assert (await client.put("/api/workspace/controller", json=CONTROLLER)).status_code == 403


async def test_deleting_the_case_removes_its_files_from_the_storage(
    client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine
):
    case, item, _ = await setup_case(client, make_user, owner_engine)
    await client.post(
        f"/api/cases/{case['id']}/package/{item['id']}/files",
        json={"file_name": "a.pdf", "content": b64(PDF)},
    )
    assert any(k.startswith("documents/") for k in MEMORY)
    assert (await client.delete(f"/api/cases/{case['id']}")).status_code == 204
    assert not any(k.startswith("documents/") for k in MEMORY)


async def test_deleting_the_card_removes_its_portal_files_from_the_storage(
    client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine
):
    case, item, _ = await setup_case(client, make_user, owner_engine)
    await client.post(
        f"/api/cases/{case['id']}/package/{item['id']}/files",
        json={"file_name": "a.pdf", "content": b64(PDF)},
    )
    assert (await client.delete(f"/api/contacts/{case['contact_id']}")).status_code == 204
    assert not any(k.startswith("documents/") for k in MEMORY)


async def test_a_new_workspace_key_still_opens_the_portal_files_and_links(
    client: AsyncClient,
    make_user: MakeUser,
    owner_engine: AsyncEngine,
    sessionmaker: async_sessionmaker[AsyncSession],
):
    from app.core.db import scope_to_system
    from app.core.vault import rekey_workspace

    case, item, tenant_id = await setup_case(client, make_user, owner_engine)
    token = (await portal_of(client, case))["token"]
    uploaded = await client.post(
        f"/api/cases/{case['id']}/package/{item['id']}/files",
        json={"file_name": "a.pdf", "content": b64(PDF)},
    )
    async with sessionmaker() as session:
        scope_to_system(session)
        done = await rekey_workspace(session, tenant_id)
        await session.commit()
    assert done.files == 1
    assert (await portal_of(client, case))["token"] == token
    download = await client.get(f"/api/cases/{case['id']}/package-files/{uploaded.json()['id']}")
    assert download.content == PDF


async def test_a_new_code_waits_a_minute_after_the_last_one(
    client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine
):
    case, _, _ = await setup_case(client, make_user, owner_engine)
    await staff_code(client, case)
    r = await client.post(f"/api/cases/{case['id']}/portal/code", json={"channel": "specialist"})
    assert r.status_code == 429 and r.json()["detail"] == "code_sent_recently"


async def test_erasing_the_workspace_takes_the_portal_files_along(
    client: AsyncClient,
    make_user: MakeUser,
    owner_engine: AsyncEngine,
    sessionmaker: async_sessionmaker[AsyncSession],
):
    from app.core.erase import erase_workspace

    case, item, tenant_id = await setup_case(client, make_user, owner_engine)
    uploaded = await client.post(
        f"/api/cases/{case['id']}/package/{item['id']}/files",
        json={"file_name": "a.pdf", "content": b64(PDF)},
    )
    async with sessionmaker() as session:
        scope_to_tenant(session, tenant_id)
        keys = await erase_workspace(session, tenant_id)
        await session.commit()
    assert f"documents/{tenant_id}/{uploaded.json()['id']}" in keys
    async with owner_engine.connect() as conn:
        for table in ("package_files", "client_links", "portal_consents", "portal_notices"):
            assert (await conn.execute(text(f"SELECT count(*) FROM {table}"))).scalar() == 0


# --- «Требует внимания» and the removal after the decision (with backlog 2.8) --------


async def reminders_of(client: AsyncClient) -> list[dict]:
    response = await client.get("/api/reminders")
    assert response.status_code == 200, response.text
    return response.json()


async def test_files_from_the_client_wait_in_attention_until_reviewed(
    client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine, visitor: AsyncClient
):
    case, item, _ = await setup_case(client, make_user, owner_engine)
    token = await signed_in(client, visitor, case)
    await consented(visitor, token)
    first = (await upload(visitor, token, item)).json()
    second = (await upload(visitor, token, item)).json()
    [shown] = [r for r in await reminders_of(client) if r["kind"] == "portal_uploads"]
    assert shown["case_id"] == case["id"] and shown["pending_files"] == 2
    # A file the specialist added herself waits for nobody.
    await client.post(
        f"/api/cases/{case['id']}/package/{item['id']}/files",
        json={"file_name": "a.pdf", "content": b64(PDF)},
    )
    base = f"/api/cases/{case['id']}/package-files"
    await client.post(f"{base}/{first['id']}/accept", json={})
    [shown] = [r for r in await reminders_of(client) if r["kind"] == "portal_uploads"]
    assert shown["pending_files"] == 1
    await client.post(f"{base}/{second['id']}/return", json={"reason": "unreadable"})
    assert [r for r in await reminders_of(client) if r["kind"] == "portal_uploads"] == []

    # Another specialist does not see it.
    await upload(visitor, token, item)
    await as_specialist(client, make_user, owner_engine, "boris@example.com")
    assert [r for r in await reminders_of(client) if r["kind"] == "portal_uploads"] == []


async def _decided_a_week_ago(client: AsyncClient, owner_engine: AsyncEngine, case: dict) -> None:
    body = {"stage": "result", "outcome": "visa", "decided_on": None}
    async with owner_engine.begin() as conn:
        today = (await conn.execute(text("SELECT current_date"))).scalar()
    body["decided_on"] = (today - timedelta(days=8)).isoformat()
    response = await client.patch(f"/api/cases/{case['id']}", json=body)
    assert response.status_code == 200, response.text
    async with owner_engine.begin() as conn:
        await conn.execute(text("SET LOCAL session_replication_role = replica"))
        await conn.execute(
            text("UPDATE visa_cases SET decision_entered_at = now() - interval '8 days'")
        )


async def test_the_files_go_a_week_after_the_decision_with_the_passport(
    client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine
):
    from app.core import passport_purge

    case, item, _ = await setup_case(client, make_user, owner_engine)
    added = await client.post(
        f"/api/cases/{case['id']}/package/{item['id']}/files",
        json={"file_name": "a.pdf", "content": b64(PDF)},
    )
    assert added.status_code == 201
    await _decided_a_week_ago(client, owner_engine, case)
    # No passport data in the card: the files alone make the client due.
    [due] = [r for r in await reminders_of(client) if r["kind"] == "passport_purge"]
    assert due["contact_id"] == case["contact_id"] and due["uploaded_files"] == 1

    assert await passport_purge.purge_due() == 1
    async with owner_engine.connect() as conn:
        assert (await conn.execute(text("SELECT count(*) FROM package_files"))).scalar() == 0
        details = (
            await conn.execute(
                text("SELECT details FROM audit_events WHERE action = 'contacts.passport_purged'")
            )
        ).scalar()
    assert details["files"] == 1
    assert not any(k.startswith("documents/") for k in MEMORY)
    # The package marks stay: the case keeps what was collected.
    [row] = [i for i in (await package(client, case))["items"] if i["item_id"] == item["id"]]
    assert row["state"] == "present" and row["files"] == []


async def test_removing_now_takes_the_files_too(
    client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine
):
    case, item, _ = await setup_case(client, make_user, owner_engine)
    await client.post(
        f"/api/cases/{case['id']}/package/{item['id']}/files",
        json={"file_name": "a.pdf", "content": b64(PDF)},
    )
    await _decided_a_week_ago(client, owner_engine, case)
    removed = await client.post(f"/api/reminders/passport/{case['contact_id']}/purge")
    assert removed.status_code == 204, removed.text
    async with owner_engine.connect() as conn:
        assert (await conn.execute(text("SELECT count(*) FROM package_files"))).scalar() == 0
    assert not any(k.startswith("documents/") for k in MEMORY)


# --- Off in production (owner's decision 2026-11-24) ---------------------------------


def test_the_portal_is_on_at_the_stands_and_off_in_production(monkeypatch: pytest.MonkeyPatch):
    from app.core.config import Settings

    secret = "x" * 40
    for env, on in (("local", True), ("test", True), ("staging", True), ("production", False)):
        monkeypatch.delenv("CLIENT_PORTAL", raising=False)
        assert Settings(env=env, secret_key=secret).client_portal_enabled is on, env
    assert Settings(env="production", secret_key=secret, client_portal=True).client_portal_enabled
    assert not Settings(env="staging", secret_key=secret, client_portal=False).client_portal_enabled


async def test_switched_off_the_portal_answers_like_a_link_that_never_was(
    client: AsyncClient,
    make_user: MakeUser,
    owner_engine: AsyncEngine,
    visitor: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
):
    mail.OUTBOX.clear()
    case, item, _ = await setup_case(client, make_user, owner_engine)
    token = (await portal_of(client, case))["token"]
    code = await staff_code(client, case)
    monkeypatch.setenv("CLIENT_PORTAL", "false")
    get_settings.cache_clear()
    try:
        # The client: every public address is a link that does not exist; no letter goes.
        for response in (
            await visitor.post(f"/api/portal/{token}/code"),
            await visitor.post(f"/api/portal/{token}/login", json={"code": code}),
            await visitor.get(f"/api/portal/{token}"),
            await upload(visitor, token, item),
        ):
            assert response.status_code == 404 and response.json()["detail"] == "link_invalid"
        assert mail.OUTBOX == []
        # The specialist: no link, code, files or controller details; the interface is told.
        base = f"/api/cases/{case['id']}"
        for response in (
            await client.get(f"{base}/portal"),
            await client.post(f"{base}/portal/revoke"),
            await client.post(f"{base}/portal/code", json={"channel": "specialist"}),
            await client.post(
                f"{base}/package/{item['id']}/files",
                json={"file_name": "a.pdf", "content": b64(PDF)},
            ),
            await client.get("/api/workspace/controller"),
            await client.put("/api/workspace/controller", json=CONTROLLER),
        ):
            assert response.status_code == 404, response.request.url
        assert (await client.get("/api/workspace")).json()["client_portal"] is False
        assert [r for r in await reminders_of(client) if r["kind"] == "portal_uploads"] == []
        [row] = [i for i in (await package(client, case))["items"] if i["item_id"] == item["id"]]
        assert row["files"] == []
    finally:
        monkeypatch.delenv("CLIENT_PORTAL")
        get_settings.cache_clear()
    assert (await client.get("/api/workspace")).json()["client_portal"] is True


async def test_switched_off_no_large_body_is_taken_for_the_portal(
    client: AsyncClient,
    make_user: MakeUser,
    owner_engine: AsyncEngine,
    visitor: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
):
    case, item, _ = await setup_case(client, make_user, owner_engine)
    token = await signed_in(client, visitor, case)
    await consented(visitor, token)
    monkeypatch.setenv("CLIENT_PORTAL", "false")
    get_settings.cache_clear()
    try:
        big = b"{" + b" " * (300 * 1024) + b"}"
        r = await visitor.post(
            f"/api/portal/{token}/items/{item['id']}/files",
            content=big,
            headers={"Content-Type": "application/json"},
        )
        assert r.status_code == 413
    finally:
        monkeypatch.delenv("CLIENT_PORTAL")
        get_settings.cache_clear()
