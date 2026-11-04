"""A failure the API did not expect never puts clients' data into the server log
(audit of the code, roadmap 6.2), and valid input does not fail in the database."""

import logging
from typing import Annotated

import pytest
from fastapi import Depends
from httpx import ASGITransport, AsyncClient
from pydantic import BaseModel
from sqlalchemy import String, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.api import (
    admin,
    analytics,
    bookings,
    cases,
    checklists,
    consultations,
    contacts,
    dictionaries,
    documents,
    employees,
    finances,
    imports,
    queues,
    reference,
    registrar_ledger,
    reminders,
    stay,
    subscriptions,
    templates,
    vault,
)
from app.core import db
from app.main import create_app
from app.models import (
    Booking,
    CaseExpense,
    CasePayment,
    ChecklistItem,
    Consultation,
    Contact,
    ContactDocument,
    ContactNote,
    ContactRelation,
    Country,
    CountryRule,
    DocumentTemplate,
    ImportRun,
    LeadChannel,
    OwnChecklistItem,
    Partner,
    Registrar,
    RegistrarPayment,
    ReminderDismissal,
    SubscriptionPayment,
    Tariff,
    Tenant,
    Trip,
    User,
    VacOperator,
    VaultEntry,
    VaultSystem,
    VisaCase,
    VisaCenter,
    VisaCenterNote,
    VisaPurpose,
    WaitingListEntry,
)

SECRET = "Zzsecretsurname"


def test_database_errors_do_not_quote_the_values_of_the_statement():
    """SQLAlchemy writes «[parameters: …]» (names, phones, the search text) into the
    error it raises unless the engine hides them."""
    db.get_engine.cache_clear()
    try:
        assert db.get_engine().sync_engine.hide_parameters is True
    finally:
        db.get_engine.cache_clear()


async def test_an_unexpected_error_is_a_500_and_the_log_has_no_values(
    owner_engine: AsyncEngine,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
):
    app = create_app()

    @app.post("/api/test-failure")
    async def fail(session: Annotated[AsyncSession, Depends(db.get_session)]) -> None:
        # asyncpg quotes the value it cannot send in its own message, whatever the engine
        # hides.
        await session.execute(text("SELECT CAST(:value AS integer)"), {"value": SECRET})

    # The migrations of the test database run in this process, and Alembic's logging
    # config switches off the loggers made before it.
    monkeypatch.setattr(logging.getLogger("app.core.server_errors"), "disabled", False)
    db.get_sessionmaker.cache_clear()
    db.get_engine.cache_clear()
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as http:
            with caplog.at_level(logging.ERROR):
                # An error that leaves the app is what uvicorn logs whole, message and all
                # («Exception in ASGI application»): the transport would raise it here.
                response = await http.post("/api/test-failure")
        await db.get_engine().dispose()
    finally:
        db.get_sessionmaker.cache_clear()
        db.get_engine.cache_clear()

    assert response.status_code == 500
    assert response.headers["cache-control"] == "no-store"
    assert f"POST {fail.__module__}.{fail.__qualname__} failed: DBAPIError" in caplog.text
    assert SECRET not in caplog.text


# Each request schema and the table its text fields are stored in as sent.
STORED_IN: list[tuple[type[BaseModel], type[db.Base]]] = [
    (admin.SpecialistIn, Tenant),
    (admin.SpecialistIn, User),
    (admin.SpecialistUpdate, Tenant),
    (analytics.NoteIn, VisaCenterNote),
    (bookings.BookingIn, Booking),
    (bookings.BookingUpdate, Booking),
    (cases.ApplicantIn, Contact),
    (cases.CaseUpdate, VisaCase),
    (checklists.ItemIn, ChecklistItem),
    (checklists.ItemIn, OwnChecklistItem),
    (checklists.ItemUpdate, ChecklistItem),
    (checklists.ItemUpdate, OwnChecklistItem),
    (consultations.ConsultationIn, Consultation),
    (consultations.ConsultationUpdate, Consultation),
    (contacts.ContactIn, Contact),
    (contacts.ContactUpdate, Contact),
    (contacts.NoteIn, ContactNote),
    (contacts.RelationIn, ContactRelation),
    (dictionaries.EntryIn, LeadChannel),
    (dictionaries.EntryUpdate, LeadChannel),
    (dictionaries.PurposeIn, VisaPurpose),
    (dictionaries.PurposeUpdate, VisaPurpose),
    (dictionaries.PartnerIn, Partner),
    (dictionaries.PartnerUpdate, Partner),
    (dictionaries.RegistrarIn, Registrar),
    (dictionaries.RegistrarUpdate, Registrar),
    (dictionaries.SystemIn, VaultSystem),
    (dictionaries.SystemUpdate, VaultSystem),
    (documents.DocumentIn, ContactDocument),
    (employees.EmployeeIn, User),
    (employees.EmployeeUpdate, User),
    (finances.ExpenseIn, CaseExpense),
    (finances.ExpenseUpdate, CaseExpense),
    (finances.PaymentIn, CasePayment),
    (finances.PaymentUpdate, CasePayment),
    (imports.ImportIn, ImportRun),
    (queues.QueueUpdate, WaitingListEntry),
    (reference.CountryIn, Country),
    (reference.CountryUpdate, Country),
    (reference.OperatorIn, VacOperator),
    (reference.OperatorUpdate, VacOperator),
    (reference.RuleIn, CountryRule),
    (reference.RuleUpdate, CountryRule),
    (reference.VisaCenterUpdate, VisaCenter),
    (registrar_ledger.PaymentIn, RegistrarPayment),
    (registrar_ledger.PaymentUpdate, RegistrarPayment),
    (reminders.DismissIn, ReminderDismissal),
    (stay.TripIn, Trip),
    (stay.TripUpdate, Trip),
    (subscriptions.PaymentIn, SubscriptionPayment),
    (subscriptions.TariffIn, Tariff),
    (subscriptions.TariffUpdate, Tariff),
    (templates.FileIn, DocumentTemplate),
    (templates.TemplateIn, DocumentTemplate),
    (templates.TemplateUpdate, DocumentTemplate),
    (vault.EntryIn, VaultEntry),
    (vault.EntryUpdate, VaultEntry),
]
# Fields the router shortens or rewrites before storing: a longer input is fine.
REWRITTEN = {
    (Contact, "phone"),  # E.164, app/core/phone.py
    (ContactDocument, "file_name"),  # clean_file_name
    (DocumentTemplate, "source_name"),  # clean_source_name
}


def _max_lengths(schema: type[BaseModel]) -> dict[str, int]:
    return {
        name: variant["maxLength"]
        for name, field in schema.model_json_schema()["properties"].items()
        for variant in (field, *field.get("anyOf", ()))
        if "maxLength" in variant
    }


@pytest.mark.parametrize(
    ("schema", "model"),
    STORED_IN,
    ids=[f"{s.__module__.rsplit('.', 1)[-1]}.{s.__name__}-{m.__name__}" for s, m in STORED_IN],
)
def test_text_the_api_accepts_fits_its_column(schema: type[BaseModel], model: type[db.Base]):
    """Longer text than the column holds passes the schema and fails in the database: a
    500 instead of a 422."""
    columns = model.__table__.columns
    stored = {name: limit for name, limit in _max_lengths(schema).items() if name in columns}
    assert stored, "no field of the schema is stored in this table"
    too_long = {
        name: (limit, column.type.length)
        for name, limit in stored.items()
        if isinstance((column := columns[name]).type, String)
        and column.type.length is not None  # Text: no limit
        and limit > column.type.length
        and (model, name) not in REWRITTEN
    }
    assert too_long == {}
