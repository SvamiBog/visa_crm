"""Waiting lists and application attempts (backlog 1.6)."""

from datetime import date, timedelta

from httpx import AsyncClient

from tests.helpers import MakeUser, login

CONTACTS = "/api/contacts"


async def as_specialist(client: AsyncClient, make_user: MakeUser, email="anna@example.com"):
    tenant_id = await make_user(email)
    await login(client, email)
    return tenant_id


async def countries(client: AsyncClient) -> dict[str, str]:
    return {c["code"]: c["id"] for c in (await client.get("/api/reference/countries")).json()}


async def contact(client: AsyncClient, name: str = "Анна") -> str:
    response = await client.post(CONTACTS, json={"full_name": name})
    assert response.status_code == 201, response.text
    return response.json()["id"]


async def open_cases(
    client: AsyncClient, contact_id: str, destination="PL", applicants=None
) -> list[dict]:
    codes = await countries(client)
    response = await client.post(
        f"{CONTACTS}/{contact_id}/cases",
        json={
            "destination_id": codes[destination],
            "submission_country_id": codes["BY"],
            "applicants": applicants or [{"contact_id": contact_id}],
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


async def queue(client: AsyncClient, case_id: str, queued_on: str, **body):
    return await client.post(f"/api/cases/{case_id}/queue", json={"queued_on": queued_on, **body})


async def test_waiting_list(client: AsyncClient, make_user: MakeUser):
    await as_specialist(client, make_user)
    [case] = await open_cases(client, await contact(client))
    url = f"/api/cases/{case['id']}"
    numbered = await client.patch(
        url,
        json={
            "vac_number": " PL-48213 ",
            "application_email": "anna.k.pl@mail.by",
            "application_email_owner": "specialist",
        },
    )
    assert numbered.status_code == 200, numbered.text
    assert numbered.json()["vac_number"] == "PL-48213"
    cleared = await client.patch(url, json={"application_email": " "})
    assert cleared.status_code == 200 and cleared.json()["application_email"] is None
    bad = await client.patch(url, json={"application_email": "not-an-email"})
    assert bad.status_code == 422
    await client.patch(url, json={"application_email": "anna.k.pl@mail.by"})
    assert numbered.json()["booking_type"] == "waiting_list"
    assert numbered.json()["identifier_type"] == "vac_id"

    response = await queue(client, case["id"], "2026-06-12")
    assert response.status_code == 201, response.text
    [entry] = response.json()
    assert entry["status"] == "in_queue"
    again = await queue(client, case["id"], "2026-06-13")
    assert again.status_code == 409 and again.json()["detail"] == "already_queued"

    # The letter with the date: the date is the submission date, the case moves on.
    dated = await client.patch(
        f"/api/queue-entries/{entry['id']}",
        json={"letter_on": "2026-10-10", "appointment_on": "2026-11-20"},
    )
    assert dated.status_code == 200, dated.text
    assert dated.json()["status"] == "date_received"
    assert dated.json()["days_waiting"] == 120
    shown = (await client.get(url)).json()
    assert shown["submission_on"] == "2026-11-20" and shown["stage"] == "documents"
    assert [e["id"] for e in shown["queue_entries"]] == [entry["id"]]
    # The wait is counted in the average for Poland (no city or purpose here).
    assert shown["average_wait_days"] == 120


async def test_taken_off_and_queued_again(client: AsyncClient, make_user: MakeUser):
    await as_specialist(client, make_user)
    [case] = await open_cases(client, await contact(client))
    [entry] = (await queue(client, case["id"], "2026-03-01")).json()
    url = f"/api/queue-entries/{entry['id']}"
    early = await client.patch(url, json={"letter_on": "2026-02-01"})
    assert early.status_code == 422
    reason_only = await client.patch(url, json={"removed_reason": "передумал"})
    assert reason_only.status_code == 422
    removed = await client.patch(url, json={"removed_on": "2026-05-01", "removed_reason": "ушёл"})
    assert removed.json()["status"] == "removed"
    assert removed.json()["days_waiting"] == 61

    assert (await queue(client, case["id"], "2026-06-01")).status_code == 201
    # The old entry cannot be reopened while the new one is open.
    reopened = await client.patch(url, json={"removed_on": None})
    assert reopened.status_code == 409


async def test_group_goes_into_the_queue_together(client: AsyncClient, make_user: MakeUser):
    await as_specialist(client, make_user)
    anna = await contact(client)
    family = await open_cases(
        client,
        anna,
        applicants=[{"contact_id": anna}, {"full_name": "Сергей"}, {"full_name": "Мария"}],
    )
    await queue(client, family[2]["id"], "2026-06-01")
    response = await queue(client, family[0]["id"], "2026-06-12", with_group=True)
    assert response.status_code == 201
    assert {e["case_id"] for e in response.json()} == {family[0]["id"], family[1]["id"]}


async def test_application_attempts(client: AsyncClient, make_user: MakeUser):
    await as_specialist(client, make_user)
    anna = await contact(client)
    [france] = await open_cases(client, anna, destination="FR")
    assert france["booking_type"] == "windowed_application" and france["window_days"] == 60
    url = f"/api/cases/{france['id']}/attempts"
    today = date.today()

    old = (
        await client.post(url, json={"sent_on": (today - timedelta(days=70)).isoformat()})
    ).json()
    assert old["number"] == 1 and old["status"] == "expired"
    # Sending again closes the expired attempt and opens the next one.
    second = await client.post(url, json={"sent_on": today.isoformat()})
    assert second.status_code == 201, second.text
    second = second.json()
    assert second["number"] == 2 and second["status"] == "open"
    assert second["deadline_on"] == (today + timedelta(days=60)).isoformat()
    assert second["days_left"] == 60
    busy = await client.post(url, json={"sent_on": today.isoformat()})
    assert busy.status_code == 409 and busy.json()["detail"] == "attempt_open"

    came = await client.patch(
        f"/api/attempts/{second['id']}",
        json={"outcome": "date_received", "appointment_on": "2026-12-15"},
    )
    assert came.status_code == 200, came.text
    assert came.json()["status"] == "date_received"
    assert came.json()["closed_on"] == today.isoformat()
    shown = (await client.get(f"/api/cases/{france['id']}")).json()
    assert [a["number"] for a in shown["attempts"]] == [2, 1]
    assert shown["attempts"][1]["outcome"] == "expired"
    assert shown["submission_on"] == "2026-12-15" and shown["stage"] == "documents"

    # Poland has a waiting list, not a window.
    [poland] = await open_cases(client, anna)
    no_window = await client.post(
        f"/api/cases/{poland['id']}/attempts", json={"sent_on": today.isoformat()}
    )
    assert no_window.status_code == 422 and no_window.json()["detail"] == "no_window"


async def test_country_queue_view(client: AsyncClient, make_user: MakeUser):
    await as_specialist(client, make_user)
    codes = await countries(client)
    [anna] = await open_cases(client, await contact(client, "Анна"))
    [boris] = await open_cases(client, await contact(client, "Борис"))
    [vera] = await open_cases(client, await contact(client, "Вера"))
    [france] = await open_cases(client, await contact(client, "Галина"), destination="FR")
    await client.patch(f"/api/cases/{anna['id']}", json={"vac_number": "PL-1"})
    [a] = (await queue(client, anna["id"], "2026-05-01")).json()
    [b] = (await queue(client, boris["id"], "2026-06-01")).json()
    [v] = (await queue(client, vera["id"], "2026-04-01")).json()
    await client.patch(f"/api/queue-entries/{b['id']}", json={"letter_on": "2026-09-29"})
    await client.patch(f"/api/queue-entries/{v['id']}", json={"removed_on": "2026-04-20"})
    await client.post(
        f"/api/cases/{france['id']}/attempts", json={"sent_on": date.today().isoformat()}
    )

    view = (await client.get("/api/queues", params={"country_id": codes["PL"]})).json()
    assert [(r["full_name"], r["status"]) for r in view["entries"]] == [
        ("Анна", "in_queue"),
        ("Борис", "date_received"),
    ]
    assert view["entries"][0]["vac_number"] == "PL-1"
    assert view["averages"] == [
        {"visa_center_id": None, "purpose_id": None, "average_days": 120, "entries": 1}
    ]
    everyone = (
        await client.get("/api/queues", params={"country_id": codes["PL"], "closed": True})
    ).json()
    assert {r["status"] for r in everyone["entries"]} == {"in_queue", "date_received", "removed"}
    assert a["status"] == "in_queue"

    french = (await client.get("/api/queues", params={"country_id": codes["FR"]})).json()
    assert [(r["full_name"], r["status"]) for r in french["attempts"]] == [("Галина", "open")]

    summary = {row["country_id"]: row for row in (await client.get("/api/queues/summary")).json()}
    assert (summary[codes["PL"]]["waiting"], summary[codes["PL"]]["dated"]) == (1, 1)
    assert summary[codes["FR"]]["open_attempts"] == 1


async def test_search_by_visa_center_number(client: AsyncClient, make_user: MakeUser):
    await as_specialist(client, make_user)
    [case] = await open_cases(client, await contact(client, "Анна"))
    await open_cases(client, await contact(client, "Борис"))
    await client.patch(f"/api/cases/{case['id']}", json={"vac_number": "PL-48213"})
    found = (await client.post(f"{CONTACTS}/search", json={"query": "48213"})).json()
    assert [row["full_name"] for row in found["items"]] == ["Анна"]


async def test_other_workspaces_queues_are_not_found(client: AsyncClient, make_user: MakeUser):
    await as_specialist(client, make_user)
    codes = await countries(client)
    [case] = await open_cases(client, await contact(client))
    [entry] = (await queue(client, case["id"], "2026-06-12")).json()
    [france] = await open_cases(client, await contact(client), destination="FR")
    attempt = (
        await client.post(
            f"/api/cases/{france['id']}/attempts", json={"sent_on": date.today().isoformat()}
        )
    ).json()
    client.cookies.clear()

    await as_specialist(client, make_user, "bella@example.com")
    assert (await queue(client, case["id"], "2026-06-12")).status_code == 404
    entry_patch = await client.patch(
        f"/api/queue-entries/{entry['id']}", json={"letter_on": "2026-07-01"}
    )
    assert entry_patch.status_code == 404
    attempt_patch = await client.patch(
        f"/api/attempts/{attempt['id']}", json={"outcome": "cancelled"}
    )
    assert attempt_patch.status_code == 404
    view = (await client.get("/api/queues", params={"country_id": codes["PL"]})).json()
    assert view == {"entries": [], "attempts": [], "averages": []}
    assert (await client.get("/api/queues/summary")).json() == []


async def test_log_has_no_numbers_or_emails(client: AsyncClient, make_user: MakeUser):
    await as_specialist(client, make_user)
    [case] = await open_cases(client, await contact(client))
    await client.patch(
        f"/api/cases/{case['id']}",
        json={"vac_number": "PL-77777", "application_email": "secret@mail.by"},
    )
    [entry] = (await queue(client, case["id"], "2026-06-12")).json()
    await client.patch(f"/api/queue-entries/{entry['id']}", json={"removed_on": "2026-07-01"})
    events = (await client.get("/api/audit")).json()["events"]
    actions = [e["action"] for e in events[:3]]
    assert actions == ["cases.queue_updated", "cases.queued", "cases.updated"]
    assert "PL-77777" not in str(events) and "secret@" not in str(events)
