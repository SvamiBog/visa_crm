"""Cards before the import (backlog 1.22, docs/data-format.md → «Карточки до загрузки»): each
client of the file as the CRM will have them once the file is loaded — the card, its notes,
consultations, cases with their money, queue, applications and bookings, and accounts —
taken from the run of the check before it rolls back. A record the file adds or changes is
marked, so the specialist sees what the file will do before she confirms it.

The fields are the format's columns (app/core/data_format.py) in the interface's language:
the specialist reads her file's words. A password is only «есть».

The cards hold the clients' data: they stay in the memory of the web process with the file
(app/core/import_uploads.py), never in the database or the log.
"""

import uuid
from collections import defaultdict
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Literal

from sqlalchemy import select

from app.core.data_format import SHEETS, Column, Sheet, ValueType
from app.core.data_import import PASSPORT
from app.core.finance import balance, money
from app.models import (
    ApplicationAttempt,
    Booking,
    CaseExpense,
    CaseFinance,
    CasePayment,
    Consultation,
    Contact,
    ContactNote,
    Country,
    LeadChannel,
    Partner,
    Registrar,
    VaultEntry,
    VaultSystem,
    VisaCase,
    VisaCenter,
    VisaPurpose,
    WaitingListEntry,
)

if TYPE_CHECKING:
    from app.core.data_import import WorkbookImport

# Read again from the session: what the run wrote, with the database's defaults filled in.
_FRESH = {"populate_existing": True}

Change = Literal["new", "updated"] | None
Status = Literal["new", "updated", "same"]


@dataclass(frozen=True)
class Localized:
    """A name the CRM keeps in both languages: a country, a city."""

    ru: str
    en: str


@dataclass(frozen=True)
class Record:
    """One record as a row of its sheet: column key → value."""

    sheet: str
    change: Change
    values: dict[str, Any]
    # A case's own records: payments, expenses, queue, applications, bookings, accounts.
    children: tuple["Record", ...] = ()


@dataclass(frozen=True)
class Card:
    status: Status
    # None: the specialist's own accounts, a card of no client.
    name: str | None
    phone: str | None
    records: tuple[Record, ...]
    # The report's lines of the client's rows: where and why, never a value.
    issues: tuple[dict[str, Any], ...] = ()


async def cards(run: "WorkbookImport") -> list[Card]:
    """The cards of the clients the run's rows were about, in the file's order; the
    specialist's own accounts last. Call before the run's session rolls back: the cards
    are read from it, with what the run wrote."""
    if not run.touched:
        return []
    builder = _Builder(run)
    await builder.load()
    made = [builder.card(cid, issues) for cid, issues in run.touched.items() if cid is not None]
    if None in run.touched:
        made.append(builder.own(run.touched[None]))
    return [card for card in made if card is not None]


def _by(records: Any, key: str) -> defaultdict[Any, list[Any]]:
    of: defaultdict[Any, list[Any]] = defaultdict(list)
    for record in records:
        of[getattr(record, key)].append(record)
    return of


def _word(value: Any) -> str | None:
    """A fixed list's value: the choice of the format it stands for."""
    return value.value if value is not None else None


def _walk(records: tuple[Record, ...]) -> Iterator[Record]:
    for record in records:
        yield record
        yield from _walk(record.children)


class _Builder:
    """The workspace's records as the run left them, and the cards made of them."""

    def __init__(self, run: "WorkbookImport") -> None:
        self.run = run
        self.base = run.base
        self.finances_on = "finances" in run.modules
        self.vault_on = "vault" in run.modules

    async def load(self) -> None:
        s = self.run.session

        async def every(model: Any) -> list[Any]:
            return list(await s.scalars(select(model), execution_options=_FRESH))

        self.names: dict[uuid.UUID, str] = {}
        for model in (LeadChannel, Partner, VisaPurpose, Registrar, VaultSystem):
            self.names |= dict((await s.execute(select(model.id, model.name))).tuples().all())
        self.countries: dict[uuid.UUID, Localized] = {}
        self.by_code: dict[str, Localized] = {}
        for cid, code, ru, en in await s.execute(
            select(Country.id, Country.code, Country.name_ru, Country.name_en)
        ):
            self.countries[cid] = self.by_code[code] = Localized(ru, en)
        # The English name of a city is optional: the Russian one stands in for it.
        self.cities = {
            center_id: Localized(ru, en or ru)
            for center_id, ru, en in await s.execute(
                select(VisaCenter.id, VisaCenter.city_ru, VisaCenter.city_en)
            )
        }
        self.contacts: dict[uuid.UUID, Contact] = {c.id: c for c in await every(Contact)}
        self.notes = _by(await every(ContactNote), "contact_id")
        self.consultations = _by(await every(Consultation), "contact_id")
        cases = await every(VisaCase)
        self.cases = _by(cases, "contact_id")
        self.families = _by([c for c in cases if c.group_id is not None], "group_id")
        self.finances: dict[uuid.UUID, CaseFinance] = {
            f.case_id: f for f in await every(CaseFinance)
        }
        self.payments = _by(await every(CasePayment), "case_id")
        self.expenses = _by(await every(CaseExpense), "case_id")
        self.entries = _by(await every(WaitingListEntry), "case_id")
        self.attempts = _by(await every(ApplicationAttempt), "case_id")
        self.bookings = _by(await every(Booking), "case_id")
        accounts = await every(VaultEntry) if self.vault_on else []
        self.logins = {a.id: a.login for a in accounts}
        self.accounts_of_case = _by([a for a in accounts if a.case_id], "case_id")
        self.accounts = _by([a for a in accounts if not a.case_id], "contact_id")

    def change(self, record_id: uuid.UUID) -> Change:
        if record_id in self.run.made:
            return "new"
        return "updated" if record_id in self.run.changed else None

    # --- Cards --------------------------------------------------------------------------

    def card(self, contact_id: uuid.UUID, issues: list[dict[str, Any]]) -> Card | None:
        contact = self.contacts.get(contact_id)
        if contact is None:
            # Made by a row the database refused: rolled back, it is no card.
            return None
        client = self._client(contact)
        records = (
            client,
            *(self._note(n) for n in sorted(self.notes[contact.id], key=lambda n: n.created_at)),
            *(
                self._consultation(c)
                for c in sorted(self.consultations[contact.id], key=lambda c: c.held_on)
            ),
            *(self._case(c) for c in sorted(self.cases[contact.id], key=lambda c: c.number)),
            *self._accounts(self.accounts[contact.id]),
        )
        changed = any(r.change for r in _walk(records))
        status: Status = "new" if client.change == "new" else "updated" if changed else "same"
        return Card(status, contact.full_name, contact.phone, records, tuple(issues))

    def own(self, issues: list[dict[str, Any]]) -> Card:
        """The specialist's own accounts: the card of no client."""
        records = tuple(self._accounts(self.accounts[None]))
        changes = [r.change for r in records]
        status: Status = (
            "new"
            if changes and all(c == "new" for c in changes)
            else "updated"
            if any(changes)
            else "same"
        )
        return Card(status, None, None, records, tuple(issues))

    # --- Records ------------------------------------------------------------------------

    def _client(self, c: Contact) -> Record:
        passport = {key: (c.questionnaire or {}).get(key) for key in PASSPORT}
        passport["nationality"] = self.by_code.get(passport["nationality"] or "")
        return Record(
            "clients",
            self.change(c.id),
            {
                "last_name": c.last_name,
                "first_name": c.first_name,
                "middle_name": c.middle_name,
                "last_name_latin": c.last_name_latin,
                "first_name_latin": c.first_name_latin,
                "phone": c.phone,
                "telegram": f"@{c.telegram}" if c.telegram else None,
                "messenger": _word(c.messenger),
                "messenger_contact": c.messenger_contact,
                "email": c.email,
                "birth_date": c.birth_date,
                "channel": self.names.get(c.lead_channel_id),  # type: ignore[arg-type]
                "partner": self.names.get(c.partner_id),  # type: ignore[arg-type]
                "consent_signed_on": c.consent_signed_on,
                "contract_number": c.contract_number,
                "contract_signed_on": c.contract_signed_on,
                **passport,
            },
        )

    def _note(self, n: ContactNote) -> Record:
        written = n.created_at.astimezone(self.run.zone).date()
        return Record("notes", self.change(n.id), {"written_on": written, "body": n.body})

    def _consultation(self, c: Consultation) -> Record:
        fee = c.fee_amount is not None
        return Record(
            "consultations",
            self.change(c.id),
            {
                "held_on": c.held_on,
                "applicants": c.applicants,
                "destinations": [
                    self.countries[d] for d in c.destination_ids if d in self.countries
                ],
                "decision": _word(c.decision),
                "channel": self.names.get(c.lead_channel_id),  # type: ignore[arg-type]
                "trip_planned_on": c.trip_planned_on,
                "follow_up_on": c.follow_up_on,
                "fee_amount": c.fee_amount,
                "fee_currency": c.fee_currency if fee else None,
                # «Оплачено: нет» says something only when there is a price.
                "fee_paid": c.fee_paid if fee else None,
                "fee_paid_on": c.fee_paid_on,
                "notes": c.notes,
            },
        )

    def _case(self, case: VisaCase) -> Record:
        family = [
            self.contacts[other.contact_id].full_name
            for other in self.families.get(case.group_id, [])  # type: ignore[arg-type]
            if other.id != case.id and other.contact_id in self.contacts
        ]
        values: dict[str, Any] = {
            "number": case.number,
            "family": ", ".join(dict.fromkeys(family)) or None,
            "destination": self.countries.get(case.destination_id),
            "submission_country": self.countries.get(case.submission_country_id),
            "purpose": self.names.get(case.purpose_id),  # type: ignore[arg-type]
            "city": self.cities.get(case.visa_center_id),  # type: ignore[arg-type]
            "hall": _word(case.hall),
            "registrar": self.names.get(case.registrar_id),  # type: ignore[arg-type]
            "vac_number": case.vac_number,
            "application_email": case.application_email,
            "application_email_owner": _word(case.application_email_owner),
            "stage": case.stage.value,
            "opened_on": case.created_at.astimezone(self.run.zone).date(),
            "questionnaire_due_on": case.questionnaire_due_on,
            "questionnaire_received_on": case.questionnaire_received_on,
            "submission_on": case.submission_on,
            "outcome": _word(case.outcome),
            "visa_entries": _word(case.visa_entries),
            "after_appeal": True if case.after_appeal else None,
            "decided_on": case.decided_on,
            "visa_valid_from": case.visa_valid_from,
            "visa_valid_to": case.visa_valid_to,
            "stay_days": case.stay_days,
        }
        payments = sorted(self.payments[case.id], key=lambda p: (p.paid_on, p.amount))
        if self.finances_on:
            values |= self._money(case, payments)
        children: list[Record] = []
        if self.finances_on:
            children += [self._payment(p) for p in payments]
            children += [
                self._expense(e) for e in sorted(self.expenses[case.id], key=lambda e: e.spent_on)
            ]
        children += [
            self._entry(e) for e in sorted(self.entries[case.id], key=lambda e: e.queued_on)
        ]
        children += [
            self._attempt(a) for a in sorted(self.attempts[case.id], key=lambda a: a.number)
        ]
        children += [
            self._booking(b)
            for b in sorted(
                self.bookings[case.id], key=lambda b: (b.starts_on or date.max, b.place)
            )
        ]
        children += self._accounts(self.accounts_of_case[case.id])
        return Record("cases", self.change(case.id), values, tuple(children))

    def _money(self, case: VisaCase, payments: list[CasePayment]) -> dict[str, Any]:
        """The case's finances; «Оплачено» and «Остаток» as the case counts them."""
        f = self.finances.get(case.id)
        paid = [p.amount_base for p in payments]
        values: dict[str, Any] = {}
        if f is not None:
            talon = f.registrar_fee_amount is not None
            values = {
                "price": f.price,
                "prepayment": f.prepayment,
                "consultation_credit": f.consultation_credit or None,
                "registrar_fee": f.registrar_fee_amount,
                "registrar_fee_currency": f.registrar_fee_currency if talon else None,
                "registrar_fee_rate": f.registrar_fee_rate
                if talon and f.registrar_fee_currency != self.base
                else None,
                "registrar_fee_on": f.registrar_fee_on if talon else None,
                # Paid before the CRM or not: whether the registrar is owed the talon.
                "registrar_fee_settled": f.registrar_fee_settled if talon else None,
                "partner_terms": _word(f.partner_terms),
                "partner_percent": f.partner_percent,
                "partner_fixed": f.partner_fixed,
                "partner_paid_on": f.partner_paid_on,
            }
        if f is not None or paid:
            values["paid"] = money(sum(paid, Decimal(0)))
            values["balance"] = balance(
                f.price if f else None, f.consultation_credit if f else Decimal(0), paid
            )
        return values

    def _payment(self, p: CasePayment) -> Record:
        return Record(
            "payments",
            self.change(p.id),
            {
                "paid_on": p.paid_on,
                "amount": p.amount,
                "currency": p.currency,
                "rate": p.rate if p.currency != self.base else None,
                "official": True if p.official else None,
                "note": p.note,
            },
        )

    def _expense(self, e: CaseExpense) -> Record:
        return Record(
            "expenses",
            self.change(e.id),
            {
                "spent_on": e.spent_on,
                "description": e.description,
                "amount": e.amount,
                "currency": e.currency,
                "rate": e.rate if e.currency != self.base else None,
            },
        )

    def _entry(self, e: WaitingListEntry) -> Record:
        return Record(
            "queue",
            self.change(e.id),
            {
                "queued_on": e.queued_on,
                "letter_on": e.letter_on,
                "removed_on": e.removed_on,
                "removed_reason": e.removed_reason,
            },
        )

    def _attempt(self, a: ApplicationAttempt) -> Record:
        return Record(
            "attempts",
            self.change(a.id),
            {
                "number": a.number,
                "sent_on": a.sent_on,
                "deadline_on": a.deadline_on,
                "outcome": _word(a.outcome),
                "closed_on": a.closed_on,
            },
        )

    def _booking(self, b: Booking) -> Record:
        return Record(
            "bookings",
            self.change(b.id),
            {
                "kind": b.kind.value,
                "status": b.status.value,
                "place": b.place,
                "starts_on": b.starts_on,
                "ends_on": b.ends_on,
                "free_cancel_until": b.free_cancel_until,
                "valid_until": b.valid_until,
                "reference": b.reference,
                "account": self.logins.get(b.vault_entry_id),  # type: ignore[arg-type]
                "note": b.note,
            },
        )

    def _accounts(self, accounts: list[VaultEntry]) -> list[Record]:
        return [
            Record(
                "accounts",
                self.change(a.id),
                {
                    "system": self.names.get(a.system_id),
                    "login": a.login,
                    # Never the password: only that there is one.
                    "password": True if a.password_encrypted else None,
                    "email_owner": _word(a.email_owner),
                    "created_manually": True if a.created_manually else None,
                    "extra_id": a.extra_id,
                    "note": a.note,
                },
            )
            for a in sorted(accounts, key=lambda a: (self.names.get(a.system_id, ""), a.login))
        ]


def summary(card: Card, n: int) -> dict[str, Any]:
    """A line of the list of cards."""
    return {
        "n": n,
        "status": card.status,
        "name": card.name,
        "phone": card.phone,
        "consultations": sum(r.sheet == "consultations" for r in card.records),
        "cases": sum(r.sheet == "cases" for r in card.records),
        "issues": len(card.issues),
    }


def render(card: Card, language: str) -> dict[str, Any]:
    """The card for the page: sections by sheet, fields with the format's headers and
    words in the language; empty fields are left out."""
    return {
        "status": card.status,
        "name": card.name,
        "phone": card.phone,
        "sections": _sections(card.records, language),
        "issues": list(card.issues),
    }


# How the page shows a value of the format's type; the rest is text.
_SHOWN_AS = {
    ValueType.DATE: "date",
    ValueType.MONEY: "money",
    ValueType.INT: "number",
    ValueType.RATE: "number",
    ValueType.PERCENT: "number",
    ValueType.BOOL: "bool",
    ValueType.SECRET: "secret",
}


def _sections(records: tuple[Record, ...], language: str) -> list[dict[str, Any]]:
    sections = []
    for sheet in SHEETS:
        mine = [r for r in records if r.sheet == sheet.key]
        if mine:
            sections.append(
                {
                    "sheet": sheet.key,
                    "title": getattr(sheet, language),
                    "records": [_record(sheet, r, language) for r in mine],
                }
            )
    return sections


def _record(sheet: Sheet, record: Record, language: str) -> dict[str, Any]:
    fields = [
        {
            "key": column.key,
            "label": getattr(column, language),
            "type": _SHOWN_AS.get(column.type, "text"),
            "value": _value(column, value, language),
        }
        for column in sheet.columns
        if (value := record.values.get(column.key)) not in (None, [])
    ]
    return {
        "change": record.change,
        "fields": fields,
        "sections": _sections(record.children, language),
    }


def _value(column: Column, value: Any, language: str) -> str | bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, Localized):
        return getattr(value, language)
    if isinstance(value, list):
        return ", ".join(str(_value(column, item, language)) for item in value)
    if column.choices:
        choice = next(c for c in column.choices if c.value == value)
        return getattr(choice, language)
    if isinstance(value, date):
        return value.isoformat()
    return str(value)
