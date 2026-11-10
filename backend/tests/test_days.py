"""Dates the CRM takes (app/core/days.py): a year typed wrong is refused where it comes in
— the API and an import file — and never breaks a card or «Требует внимания»."""

from datetime import date
from typing import Annotated, Any, get_args, get_origin

from annotated_types import Ge, Le
from fastapi.dependencies.models import Dependant
from fastapi.routing import APIRoute
from httpx import AsyncClient
from pydantic import BaseModel
from pydantic.fields import FieldInfo

from app.core.days import FIRST_DAY, LAST_DAY, in_range
from app.core.questionnaire import Passport, Questionnaire, VisaRecord
from app.main import create_app
from tests.helpers import MakeUser, login, workspace_today

# «26» typed for the year stays the year 26 in a browser's date field.
TYPO = "0026-11-05"
FAR = "9999-12-31"
# Kept on the card as JSON and read back through the same models, so a bound added to
# them would stop a card saved before it from opening. Their dates only feed the passport
# checks, which take any date (tests/test_consultation_checks.py).
STORED_AS_JSON = (Passport, Questionnaire, VisaRecord)


def test_the_days_taken_are_the_years_the_work_is_about():
    assert in_range(FIRST_DAY) and in_range(LAST_DAY) and in_range(date(2026, 11, 10))
    assert not in_range(date(26, 11, 5)) and not in_range(date(1899, 12, 30))
    assert not in_range(date(2101, 1, 1)) and not in_range(date.max)


def _bounded(metadata: Any) -> bool:
    found = set()
    for item in metadata:
        found |= {type(m) for m in getattr(item, "metadata", [item])}
    return {Ge, Le} <= found


def _unbounded_dates(annotation: Any, metadata: Any = ()) -> bool:
    """Whether the type takes a date (not a moment) with no first and last day."""
    if annotation is date:
        return not _bounded(metadata)
    if get_origin(annotation) is Annotated:
        base, *inner = get_args(annotation)
        return _unbounded_dates(base, inner)
    return any(_unbounded_dates(arg) for arg in get_args(annotation))


def _models(annotation: Any) -> list[type[BaseModel]]:
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return [annotation]
    return [model for arg in get_args(annotation) for model in _models(arg)]


def _open_fields(model: type[BaseModel], seen: set[type[BaseModel]]) -> list[str]:
    if model in seen or model in STORED_AS_JSON:
        return []
    seen.add(model)
    found = []
    for name, field in model.model_fields.items():
        if _unbounded_dates(field.annotation, field.metadata):
            found.append(f"{model.__name__}.{name}")
        for nested in _models(field.annotation):
            found += _open_fields(nested, seen)
    return found


def _with_dependencies(dependant: Dependant) -> list[Dependant]:
    return [dependant, *(d for sub in dependant.dependencies for d in _with_dependencies(sub))]


def _api_routes(routes: Any) -> list[APIRoute]:
    """Every route of the app; an included router keeps its own (FastAPI lists them flat
    or as the router, depending on its version)."""
    found = []
    for route in routes:
        if isinstance(route, APIRoute):
            found.append(route)
        else:
            inner = getattr(route, "original_router", route)
            found += _api_routes(getattr(inner, "routes", []))
    return found


def test_every_date_of_a_request_has_a_first_and_a_last_day():
    """A new date field or parameter is a `Day` (app/core/days.py), like the rest."""
    routes = _api_routes(create_app().routes)
    assert len(routes) > 100  # the walk reaches the routers' own routes
    found: set[str] = set()
    seen: set[type[BaseModel]] = set()
    for route in routes:
        for dependant in _with_dependencies(route.dependant):
            for param in (*dependant.query_params, *dependant.path_params):
                info: FieldInfo = param.field_info
                if _unbounded_dates(info.annotation, info.metadata):
                    found.add(f"{route.path} ?{param.name}")
            for param in dependant.body_params:
                for model in _models(param.field_info.annotation):
                    found |= set(_open_fields(model, seen))
    assert sorted(found) == []


async def test_a_year_typed_wrong_is_refused_and_nothing_is_saved(
    client: AsyncClient, make_user: MakeUser
):
    await make_user("anna@example.com")
    await login(client, "anna@example.com")
    today = workspace_today().isoformat()
    codes = {c["code"]: c["id"] for c in (await client.get("/api/reference/countries")).json()}
    contact = (await client.post("/api/contacts", json={"first_name": "Анна"})).json()
    card = f"/api/contacts/{contact['id']}"
    consultation = (
        await client.post(f"{card}/consultations", json={"held_on": today, "decision": "thinking"})
    ).json()
    [case] = (
        await client.post(
            f"{card}/cases",
            json={
                "destination_id": codes["FR"],
                "submission_country_id": codes["BY"],
                "applicants": [{"contact_id": contact["id"]}],
            },
        )
    ).json()
    attempt = (
        await client.post(f"/api/cases/{case['id']}/attempts", json={"sent_on": today})
    ).json()

    refused = [
        await client.post("/api/contacts", json={"first_name": "Пётр", "birth_date": TYPO}),
        await client.patch(card, json={"contract_signed_on": TYPO}),
        await client.post(f"{card}/consultations", json={"held_on": FAR, "decision": "thinking"}),
        await client.patch(f"/api/consultations/{consultation['id']}", json={"held_on": FAR}),
        await client.patch(f"/api/consultations/{consultation['id']}", json={"follow_up_on": TYPO}),
        await client.patch(f"/api/cases/{case['id']}", json={"submission_on": TYPO}),
        await client.patch(f"/api/cases/{case['id']}", json={"questionnaire_due_on": "0001-01-01"}),
        await client.patch(
            f"/api/cases/{case['id']}", json={"outcome": "refusal", "decided_on": TYPO}
        ),
        await client.patch(
            f"/api/cases/{case['id']}", json={"outcome": "visa", "visa_valid_to": FAR}
        ),
        await client.post(f"/api/cases/{case['id']}/queue", json={"queued_on": TYPO}),
        await client.patch(f"/api/attempts/{attempt['id']}", json={"sent_on": FAR}),
        await client.post(
            f"/api/cases/{case['id']}/bookings",
            json={"kind": "hotel", "place": "Hotel", "free_cancel_until": TYPO},
        ),
        await client.post(
            f"/api/cases/{case['id']}/payments",
            json={"paid_on": TYPO, "amount": "10", "currency": "BYN"},
        ),
        await client.get("/api/analytics", params={"start": "0001-01-01", "end": "0001-12-31"}),
        await client.get("/api/analytics", params={"start": "9999-01-01", "end": FAR}),
        await client.get("/api/audit", params={"occurred_from": "9999-12-31T00:00:00Z"}),
        await client.get("/api/audit", params={"occurred_to": "0001-01-01T00:00:00Z"}),
    ]
    assert [r.status_code for r in refused] == [422] * len(refused)

    # The card, the list of what needs attention and the home screen are as they were.
    opened = await client.get(card)
    assert opened.status_code == 200, opened.text
    assert opened.json()["cases"][0]["outcome"] is None
    assert [c["held_on"] for c in opened.json()["consultations"]] == [today]
    assert (await client.get("/api/reminders")).status_code == 200
    assert (await client.get("/api/home")).status_code == 200
    # A real date at either end of the years taken is a date like any other.
    born = await client.patch(card, json={"birth_date": FIRST_DAY.isoformat()})
    assert born.status_code == 200, born.text
