"""The workspace's records as rows of the VisaCRM data format (backlog 1.21, 1.22,
docs/data-format.md): each client with their notes, consultations, cases — with the money,
queue, applications, bookings and accounts of each — and the specialist's own accounts.
The values are the format's columns (app/core/data_format.py) as the CRM keeps them.

One reader for two uses, so they cannot drift apart: the cards before an import show these
records as the run of the check left them, with what the file adds or changes marked
(app/core/import_cards.py), and the export writes them to a file (app/core/data_export.py).
"""

import uuid
from collections import defaultdict
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any, Literal
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.data_import import PASSPORT
from app.core.finance import Paid, money, summarize, to_base
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

# Read again from the session: what a run of the import wrote, with the database's defaults.
_FRESH = {"populate_existing": True}

Change = Literal["new", "updated"] | None


def unchanged(_record_id: uuid.UUID) -> Change:
    """What an export marks: nothing, the records are as the CRM has them."""
    return None


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
    # What only the file shows: the row's code, the codes of its client, case, consultation
    # and family, and the client's name and the case's country next to them. A card shows
    # the record under its client instead, with the family by name.
    links: dict[str, Any] = field(default_factory=dict)


def walk(records: tuple[Record, ...]) -> Iterator[Record]:
    for record in records:
        yield record
        yield from walk(record.children)


def _by(records: Any, key: str) -> defaultdict[Any, list[Any]]:
    of: defaultdict[Any, list[Any]] = defaultdict(list)
    for record in records:
        of[getattr(record, key)].append(record)
    return of


def _word(value: Any) -> str | None:
    """A fixed list's value: the choice of the format it stands for."""
    return value.value if value is not None else None


def _code(record_id: uuid.UUID | None) -> str | None:
    return str(record_id) if record_id is not None else None


class Records:
    """The workspace's records in the session, read once (load), as rows of the format.
    `change` tells which records a run of the import made or changed."""

    def __init__(
        self,
        session: AsyncSession,
        *,
        base: str,
        zone: ZoneInfo,
        modules: set[str],
        change: Callable[[uuid.UUID], Change] = unchanged,
    ) -> None:
        self.session = session
        self.base = base
        self.zone = zone
        self.change = change
        self.finances_on = "finances" in modules
        self.vault_on = "vault" in modules

    async def load(self) -> None:
        s = self.session

        async def every(model: Any) -> list[Any]:
            return list(await s.scalars(select(model), execution_options=_FRESH))

        self.names: dict[uuid.UUID, str] = {}
        for model in (LeadChannel, Partner, VisaPurpose, Registrar, VaultSystem):
            self.names |= dict((await s.execute(select(model.id, model.name))).all())
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
        self.vault: dict[uuid.UUID, VaultEntry] = {a.id: a for a in accounts}
        self.logins = {a.id: a.login for a in accounts}
        self.accounts_of_case = _by([a for a in accounts if a.case_id], "case_id")
        self.accounts = _by([a for a in accounts if not a.case_id], "contact_id")

    def client(self, contact_id: uuid.UUID) -> tuple[Record, ...] | None:
        """The client's records: the card, notes, consultations, cases, accounts not of a
        case. None: no such client (made by a row the database refused, rolled back)."""
        contact = self.contacts.get(contact_id)
        if contact is None:
            return None
        name = contact.full_name
        return (
            self._client(contact),
            *(
                self._note(n, name)
                for n in sorted(self.notes[contact.id], key=lambda n: (n.created_at, n.id))
            ),
            *(
                self._consultation(c, name)
                for c in sorted(self.consultations[contact.id], key=lambda c: (c.held_on, c.id))
            ),
            *(self._case(c, name) for c in sorted(self.cases[contact.id], key=lambda c: c.number)),
            *self._accounts(self.accounts[contact.id], name),
        )

    def own(self) -> list[Record]:
        """The specialist's own accounts, of no client."""
        return self._accounts(self.accounts[None], None)

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
            links={"code": _code(c.id)},
        )

    def _of_client(self, record_id: uuid.UUID, contact_id: uuid.UUID, name: str) -> dict:
        return {"code": _code(record_id), "client": _code(contact_id), "client_name": name}

    def _note(self, n: ContactNote, name: str) -> Record:
        written = n.created_at.astimezone(self.zone).date()
        return Record(
            "notes",
            self.change(n.id),
            {"written_on": written, "body": n.body},
            links=self._of_client(n.id, n.contact_id, name),
        )

    def _consultation(self, c: Consultation, name: str) -> Record:
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
            links=self._of_client(c.id, c.contact_id, name),
        )

    def _case(self, case: VisaCase, name: str) -> Record:
        family = [
            self.contacts[other.contact_id].full_name
            for other in self.families.get(case.group_id, [])  # type: ignore[arg-type]
            if other.id != case.id and other.contact_id in self.contacts
        ]
        destination = self.countries.get(case.destination_id)
        values: dict[str, Any] = {
            "number": case.number,
            "family": ", ".join(dict.fromkeys(family)) or None,
            "destination": destination,
            "submission_country": self.countries.get(case.submission_country_id),
            "purpose": self.names.get(case.purpose_id),  # type: ignore[arg-type]
            "city": self.cities.get(case.visa_center_id),  # type: ignore[arg-type]
            "hall": _word(case.hall),
            "registrar": self.names.get(case.registrar_id),  # type: ignore[arg-type]
            "vac_number": case.vac_number,
            "application_email": case.application_email,
            "application_email_owner": _word(case.application_email_owner),
            "stage": case.stage.value,
            "opened_on": case.created_at.astimezone(self.zone).date(),
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
        payments = sorted(self.payments[case.id], key=lambda p: (p.paid_on, p.amount, p.id))
        if self.finances_on:
            values |= self._money(case, payments)
        # The children's rows name the case by its code, and its client and country beside.
        of_case = {"case": _code(case.id), "client_name": name, "country": destination}
        children: list[Record] = []
        if self.finances_on:
            children += [self._payment(p, of_case) for p in payments]
            children += [
                self._expense(e, of_case)
                for e in sorted(self.expenses[case.id], key=lambda e: (e.spent_on, e.id))
            ]
        children += [
            self._entry(e, of_case)
            for e in sorted(self.entries[case.id], key=lambda e: (e.queued_on, e.id))
        ]
        children += [
            self._attempt(a, of_case)
            for a in sorted(self.attempts[case.id], key=lambda a: a.number)
        ]
        children += [
            self._booking(b, of_case)
            for b in sorted(
                self.bookings[case.id], key=lambda b: (b.starts_on or date.max, b.place, b.id)
            )
        ]
        children += self._accounts(self.accounts_of_case[case.id], name)
        links = self._of_client(case.id, case.contact_id, name) | {
            "consultation": _code(case.consultation_id),
            # The file's «Семья» is a code: the cases of one submission group share it.
            "family": _code(case.group_id),
        }
        return Record("cases", self.change(case.id), values, tuple(children), links)

    def _money(self, case: VisaCase, payments: list[CasePayment]) -> dict[str, Any]:
        """The case's finances; «Оплачено» and «Остаток» as the case counts them. The format
        has the price in the base currency: a case priced in another one is converted."""
        f = self.finances.get(case.id)
        paid = [Paid(p.amount, p.currency, p.amount_base) for p in payments]
        values: dict[str, Any] = {}

        def in_base(value: Decimal | None) -> Decimal | None:
            return None if value is None or f is None else to_base(value, f.rate)

        if f is not None:
            talon = f.registrar_fee_amount is not None
            values = {
                "price": in_base(f.price),
                "prepayment": in_base(f.prepayment),
                "consultation_credit": in_base(f.consultation_credit) or None,
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
            values["paid"] = money(sum((p.amount_base for p in paid), Decimal(0)))
            values["balance"] = summarize(
                price=f.price if f else None,
                currency=f.currency if f else self.base,
                rate=f.rate if f else Decimal(1),
                consultation_credit=f.consultation_credit if f else Decimal(0),
                payments=paid,
                registrar_fee=None,
                partner_terms=None,
                partner_percent=None,
                partner_fixed=None,
                tax_rate=None,
                expenses=[],
            ).balance_base
        return values

    def _payment(self, p: CasePayment, of_case: dict[str, Any]) -> Record:
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
            links=of_case | {"code": _code(p.id)},
        )

    def _expense(self, e: CaseExpense, of_case: dict[str, Any]) -> Record:
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
            links=of_case | {"code": _code(e.id)},
        )

    def _entry(self, e: WaitingListEntry, of_case: dict[str, Any]) -> Record:
        return Record(
            "queue",
            self.change(e.id),
            {
                "queued_on": e.queued_on,
                "letter_on": e.letter_on,
                "removed_on": e.removed_on,
                "removed_reason": e.removed_reason,
            },
            links=of_case | {"code": _code(e.id)},
        )

    def _attempt(self, a: ApplicationAttempt, of_case: dict[str, Any]) -> Record:
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
            links=of_case | {"code": _code(a.id)},
        )

    def _booking(self, b: Booking, of_case: dict[str, Any]) -> Record:
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
            links=of_case | {"code": _code(b.id)},
        )

    def _accounts(self, accounts: list[VaultEntry], name: str | None) -> list[Record]:
        return [
            Record(
                "accounts",
                self.change(a.id),
                {
                    "system": self.names.get(a.system_id),
                    "login": a.login,
                    # Never the password: only that there is one. The export with
                    # passwords puts them in itself (app/core/data_export.py).
                    "password": True if a.password_encrypted else None,
                    "email_owner": _word(a.email_owner),
                    "created_manually": True if a.created_manually else None,
                    "extra_id": a.extra_id,
                    "note": a.note,
                },
                links={
                    "code": _code(a.id),
                    "client": _code(a.contact_id),
                    "case": _code(a.case_id),
                    "client_name": name,
                },
            )
            for a in sorted(
                accounts, key=lambda a: (self.names.get(a.system_id, ""), a.login, a.id)
            )
        ]
