"""The import of a spreadsheet (backlog 1.14, docs/decisions.md → «Импорт таблиц»).

Each row becomes a contact — found by phone, Telegram or a unique name, else created —
and the records of the import kind: a consultation, a case, a queue entry, a windowed
application or a booking, with accounts to the vault. The row is remembered by a key (the
kind, the person and what tells their rows apart), so a repeated import updates what the
row became instead of adding it again. An empty cell never erases data in the CRM.

The workspace's records are loaded once and looked up in memory. Each row runs in its own
savepoint: a row the database refuses is skipped and reported and the others go on; the
records are then loaded again, since the rollback expired them. A row is skipped for a
missing name, date or case before it changes anything.
"""

import hashlib
import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.cases import cleared_by_outcome
from app.core.import_fields import BY_KEY, CREATABLE, ImportField, ValueType
from app.core.import_files import SheetRow
from app.core.import_values import (
    Cell,
    Outcome,
    cell_text,
    name_key,
    normalize,
    parse_bool,
    parse_contacts,
    parse_date,
    parse_decision,
    parse_email,
    parse_int,
    parse_money,
    parse_outcome,
    parse_phone,
    parse_telegram,
    split_list,
)
from app.core.queues import deadline
from app.core.vault import decrypt_for_tenant, encrypt_for_tenant, get_tenant_key
from app.models import (
    ApplicationAttempt,
    Booking,
    BookingKind,
    CaseFinance,
    CasePayment,
    CaseStage,
    Consultation,
    Contact,
    ContactNote,
    Country,
    CountryRule,
    Decision,
    ImportKind,
    ImportRow,
    LeadChannel,
    Partner,
    Registrar,
    RegistrarKind,
    VaultEntry,
    VisaCase,
    VisaCenter,
    VisaOutcome,
    VisaPurpose,
    WaitingListEntry,
)
from app.models.queue import AttemptOutcome

# The report keeps this many skipped rows and warnings; the counts are always whole.
REPORT_LIMIT = 1000
STAGE_ORDER = list(CaseStage)
# Applicants in one consultation (the consultations table check).
APPLICANTS = range(1, 31)
FRESH = {"populate_existing": True}
VAULT_FIELDS = {"email_login", "email_password", "site_login", "site_password", "booking_account"}


class SkipRow(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass
class Options:
    currency: str
    submission_country_id: uuid.UUID
    # The table's country: a queue or windowed applications of one country.
    country_id: uuid.UUID | None = None
    email_system_id: uuid.UUID | None = None
    site_system_id: uuid.UUID | None = None


@dataclass
class Report:
    rows: int = 0
    imported: int = 0
    skipped: list[dict[str, Any]] = field(default_factory=list)
    skipped_count: int = 0
    warnings: list[dict[str, Any]] = field(default_factory=list)
    warnings_count: int = 0
    created: Counter[str] = field(default_factory=Counter)
    updated: Counter[str] = field(default_factory=Counter)
    # Values not found in a list: type → normalized value → the value as typed, rows.
    unknown: dict[str, dict[str, dict[str, Any]]] = field(default_factory=dict)

    def skip(self, row: int, reason: str) -> None:
        self.skipped_count += 1
        if len(self.skipped) < REPORT_LIMIT:
            self.skipped.append({"row": row, "reason": reason})

    def warn(self, row: int, column: str, reason: str) -> None:
        self.warnings_count += 1
        if len(self.warnings) < REPORT_LIMIT:
            self.warnings.append({"row": row, "column": column, "reason": reason})

    def seen_unknown(self, t: ValueType, key: str, value: str) -> None:
        item = self.unknown.setdefault(t.value, {}).setdefault(key, {"value": value, "rows": 0})
        item["rows"] += 1

    def as_json(self) -> dict[str, Any]:
        return {
            "rows": self.rows,
            "imported": self.imported,
            "skipped": self.skipped,
            "skipped_count": self.skipped_count,
            "warnings": self.warnings,
            "warnings_count": self.warnings_count,
            "created": dict(self.created),
            "updated": dict(self.updated),
            "unknown": {
                kind: [
                    {"key": key, "value": item["value"], "rows": item["rows"]}
                    for key, item in values.items()
                ]
                for kind, values in self.unknown.items()
            },
        }


@dataclass
class _Row:
    """One row in progress: parsed values, its warnings and counts."""

    number: int
    values: dict[str, Any] = field(default_factory=dict)
    raw: dict[str, str] = field(default_factory=dict)
    warnings: list[tuple[str, str]] = field(default_factory=list)
    created: Counter[str] = field(default_factory=Counter)
    updated: Counter[str] = field(default_factory=Counter)
    # List entries the row adds, kept back until the row is sure to be imported.
    new_entries: list[tuple[ValueType, str, Any]] = field(default_factory=list)


def _set(obj: Any, values: dict[str, Any]) -> bool:
    """Assign the given values, skipping empty ones; True if anything changed."""
    changed = False
    for name, value in values.items():
        if value is not None and getattr(obj, name) != value:
            setattr(obj, name, value)
            changed = True
    return changed


def _midnight(day: date) -> datetime:
    return datetime.combine(day, time.min, UTC)


def _id(entities: dict[str, str], name: str) -> uuid.UUID | None:
    return uuid.UUID(entities[name]) if name in entities else None


class Importer:
    run_id: uuid.UUID

    def __init__(
        self,
        session: AsyncSession,
        *,
        tenant_id: uuid.UUID,
        user_id: uuid.UUID,
        base_currency: str,
        kind: ImportKind,
        headers: list[str],
        columns: dict[int, str],
        options: Options,
        values: dict[str, dict[str, str]],
        today: date,
    ) -> None:
        self.session = session
        self.tenant_id = tenant_id
        self.user_id = user_id
        self.base = base_currency
        self.kind = kind
        self.headers = headers
        self.columns = columns
        self.options = options
        self.choices = values
        self.today = today
        self.report = Report()
        self.mapped = set(columns.values())
        self.occurrences: Counter[str] = Counter()
        self.locked = False
        self.vault_key: bytes | None = None

    # --- Loading ------------------------------------------------------------------------

    async def load(self) -> None:
        """The workspace's records the import looks up, by the keys it looks them up by."""
        s, kind, mapped = self.session, self.kind, self.mapped

        self.contacts: dict[uuid.UUID, Contact] = {}
        self.by_phone: dict[str, uuid.UUID] = {}
        self.by_telegram: dict[str, uuid.UUID] = {}
        self.by_name: defaultdict[str, list[uuid.UUID]] = defaultdict(list)
        for contact in await s.scalars(select(Contact), execution_options=FRESH):
            self._index_contact(contact)

        self.records: dict[str, ImportRow] = {
            r.key: r
            for r in await s.scalars(
                select(ImportRow).where(ImportRow.kind == kind), execution_options=FRESH
            )
        }
        # Cases a row of «Клиенты в работе» already became: not adopted by another row
        # (two visas to one country are two rows). A queue's rows share a case.
        self.claimed: set[str] = set()
        if kind == ImportKind.CASES:
            self.claimed = {
                r.entities["case"] for r in self.records.values() if "case" in r.entities
            }

        self.lists: dict[ValueType, dict[str, uuid.UUID]] = {}
        for t, model in (
            (ValueType.CHANNEL, LeadChannel),
            (ValueType.PARTNER, Partner),
            (ValueType.PURPOSE, VisaPurpose),
            (ValueType.REGISTRAR, Registrar),
        ):
            rows = await s.execute(select(model.id, model.name))
            self.lists[t] = {normalize(name): item_id for item_id, name in rows}
        countries: dict[str, uuid.UUID] = {}
        for cid, code, name_ru, name_en in await s.execute(
            select(Country.id, Country.code, Country.name_ru, Country.name_en)
        ):
            countries |= {code.casefold(): cid, normalize(name_ru): cid, normalize(name_en): cid}
        self.lists[ValueType.COUNTRY] = countries
        self.list_ids = {t: set(index.values()) for t, index in self.lists.items()}

        # Visa centers and windows of the rules «destination × where it is submitted».
        self.centers: dict[tuple[uuid.UUID, str], uuid.UUID] = {}
        for center_id, destination_id, city_ru, city_en in await s.execute(
            select(
                VisaCenter.id, CountryRule.destination_id, VisaCenter.city_ru, VisaCenter.city_en
            )
            .join(CountryRule, VisaCenter.rule_id == CountryRule.id)
            .where(CountryRule.submission_id == self.options.submission_country_id)
        ):
            self.centers[(destination_id, normalize(city_ru))] = center_id
            self.centers[(destination_id, normalize(city_en))] = center_id
        self.windows: dict[uuid.UUID, int | None] = {
            destination_id: window
            for destination_id, window in await s.execute(
                select(CountryRule.destination_id, CountryRule.window_days).where(
                    CountryRule.submission_id == self.options.submission_country_id
                )
            )
        }

        self.consultations: defaultdict[uuid.UUID, list[Consultation]] = defaultdict(list)
        for c in await s.scalars(select(Consultation), execution_options=FRESH):
            self.consultations[c.contact_id].append(c)
        # Consultations a case was opened from: their «yes» stays (see _consultation).
        self.with_case: set[uuid.UUID] = set(
            await s.scalars(
                select(VisaCase.consultation_id).where(VisaCase.consultation_id.is_not(None))
            )
        )

        self.cases: dict[uuid.UUID, VisaCase] = {}
        self.cases_of: defaultdict[uuid.UUID, list[VisaCase]] = defaultdict(list)
        self.entries_of: defaultdict[uuid.UUID, list[WaitingListEntry]] = defaultdict(list)
        self.attempts_of: defaultdict[uuid.UUID, list[ApplicationAttempt]] = defaultdict(list)
        self.bookings_of: defaultdict[uuid.UUID, list[Booking]] = defaultdict(list)
        self.finances: dict[uuid.UUID, CaseFinance] = {}
        self.payments: dict[uuid.UUID, CasePayment] = {}
        if kind != ImportKind.CONSULTATIONS:
            for case in await s.scalars(select(VisaCase), execution_options=FRESH):
                self.cases[case.id] = case
                self.cases_of[case.contact_id].append(case)
        if kind in (ImportKind.CASES, ImportKind.QUEUE):
            for e in await s.scalars(select(WaitingListEntry), execution_options=FRESH):
                self.entries_of[e.case_id].append(e)
        if kind == ImportKind.ATTEMPTS:
            for a in await s.scalars(select(ApplicationAttempt), execution_options=FRESH):
                self.attempts_of[a.case_id].append(a)
        if kind == ImportKind.BOOKINGS:
            for b in await s.scalars(select(Booking), execution_options=FRESH):
                self.bookings_of[b.case_id].append(b)
        if mapped & {"price", "paid"}:
            for f in await s.scalars(select(CaseFinance), execution_options=FRESH):
                self.finances[f.case_id] = f
        if "paid" in mapped:
            for p in await s.scalars(select(CasePayment), execution_options=FRESH):
                self.payments[p.id] = p

        self.vault: dict[uuid.UUID, VaultEntry] = {}
        self.accounts: dict[tuple[uuid.UUID | None, uuid.UUID, str], VaultEntry] = {}
        self.own_accounts: dict[str, uuid.UUID] = {}
        if mapped & VAULT_FIELDS:
            for v in await s.scalars(select(VaultEntry), execution_options=FRESH):
                self.vault[v.id] = v
                self.accounts[(v.contact_id, v.system_id, v.login.casefold())] = v
                self.own_accounts.setdefault(v.login.casefold(), v.id)
        if mapped & {"email_password", "site_password"} and self.vault_key is None:
            self.vault_key = await get_tenant_key(s, self.tenant_id)

        self.notes: set[tuple[uuid.UUID, str]] = set()
        if "contact_note" in mapped:
            self.notes = set(
                (await s.execute(select(ContactNote.contact_id, ContactNote.body))).all()
            )

        self.next_number = 1
        if kind != ImportKind.CONSULTATIONS:
            if not self.locked:
                # As app/api/cases.py: numbers go on per workspace under a transaction lock.
                await s.execute(
                    text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
                    {"key": f"visa_cases:{self.tenant_id}"},
                )
                self.locked = True
            last = await s.scalar(select(func.coalesce(func.max(VisaCase.number), 0)))
            self.next_number = (last or 0) + 1

    def _index_contact(self, contact: Contact) -> None:
        self.contacts[contact.id] = contact
        if contact.phone:
            self.by_phone.setdefault(contact.phone, contact.id)
        if contact.telegram:
            self.by_telegram.setdefault(contact.telegram.casefold(), contact.id)
        self.by_name[name_key(contact.full_name)].append(contact.id)

    # --- Rows ---------------------------------------------------------------------------

    async def run(self, rows: list[SheetRow]) -> Report:
        await self.load()
        self.report.rows = len(rows)
        for sheet_row in rows:
            row = self._parse(sheet_row)
            try:
                async with self.session.begin_nested():
                    await self._row(row)
            except SkipRow as skip:
                self.report.skip(row.number, skip.reason)
                continue
            except SQLAlchemyError:
                # The database refused the row (a rule the checks here do not know).
                self.report.skip(row.number, "row_error")
                await self.load()
                continue
            self.report.imported += 1
            self.report.created.update(row.created)
            self.report.updated.update(row.updated)
            for column, reason in row.warnings:
                self.report.warn(row.number, column, reason)
        return self.report

    def _header(self, *keys: str) -> str:
        for index, key in self.columns.items():
            if key in keys:
                return self.headers[index] or f"#{index + 1}"
        return ""

    def _parse(self, sheet_row: SheetRow) -> _Row:
        row = _Row(sheet_row.number)
        for index, key in self.columns.items():
            cell = sheet_row.cells[index] if index < len(sheet_row.cells) else None
            value = cell_text(cell)
            if value is None:
                continue
            spec = BY_KEY[key]
            header = self.headers[index] or f"#{index + 1}"
            row.raw[key] = value
            try:
                row.values[key] = self._value(spec, cell, value, row, header)
            except ValueError:
                row.warnings.append((header, f"bad_{spec.type.value}"))
        return row

    def _value(self, spec: ImportField, cell: Cell, value: str, row: _Row, header: str) -> Any:
        t = spec.type
        if spec.max_length is not None and len(value) > spec.max_length:
            raise ValueError("too long")
        if t in (ValueType.NAME, ValueType.TEXT, ValueType.SECRET, ValueType.CITY):
            return value  # a city is looked up with the case's country
        if t == ValueType.PHONE:
            return parse_phone(cell)
        if t == ValueType.TELEGRAM:
            return parse_telegram(cell)
        if t == ValueType.CONTACTS:
            return parse_contacts(cell)
        if t == ValueType.EMAIL:
            return parse_email(cell)
        if t == ValueType.DATE:
            return parse_date(cell, self.today)
        if t == ValueType.INT:
            return parse_int(cell)
        if t == ValueType.MONEY:
            amount, currency = parse_money(cell, self.options.currency)
            if currency != self.base and spec.key != "fee_amount":
                # Case money is kept in the base currency; another one needs a rate.
                row.warnings.append((header, "foreign_currency"))
                return None
            return amount, currency
        if t == ValueType.BOOL:
            return parse_bool(cell)
        if t == ValueType.DECISION:
            return parse_decision(cell)
        if t == ValueType.OUTCOME:
            return parse_outcome(cell)
        if t == ValueType.COUNTRIES:
            found = [
                self._lookup(ValueType.COUNTRY, part, row, header) for part in split_list(cell)
            ]
            return [c for c in found if c is not None]
        return self._lookup(t, value, row, header)

    def _lookup(self, t: ValueType, value: str, row: _Row, header: str) -> uuid.UUID | None:
        """An id in a list by name. An unknown value is added to the list (the
        workspace's lists) or skipped (countries) unless the specialist chose otherwise."""
        key = normalize(value)
        if key in self.lists[t]:
            return self.lists[t][key]
        self.report.seen_unknown(t, key, value)
        choice = self.choices.get(t.value, {}).get(key)
        if choice not in (None, "new", "skip"):
            try:
                chosen = uuid.UUID(choice)
            except ValueError:
                chosen = None
            if chosen in self.list_ids[t]:
                return chosen
        if choice == "skip" or t not in CREATABLE:
            row.warnings.append((header, "unknown_value"))
            return None
        for new_t, new_key, entry in row.new_entries:
            if (new_t, new_key) == (t, key):
                return entry.id
        common = {"id": uuid.uuid4(), "tenant_id": self.tenant_id}
        if t == ValueType.CHANNEL:
            entry: Any = LeadChannel(**common, name=value[:100], sort_order=100)
        elif t == ValueType.PURPOSE:
            entry = VisaPurpose(**common, name=value[:100], sort_order=100)
        elif t == ValueType.PARTNER:
            entry = Partner(**common, name=value[:200])
        else:
            entry = Registrar(
                **common, name=value[:200], kind=RegistrarKind.INDIVIDUAL, currency=self.base
            )
        row.new_entries.append((t, key, entry))
        return entry.id

    def _key(self, row: _Row) -> str:
        """The row's identity across imports: the kind, the table's country, the person,
        what tells their rows apart (the case's country) and which such row it is."""
        scope = str(self.options.country_id or "")
        apart = normalize(row.raw.get("destination", "")) if self.kind == ImportKind.CASES else ""
        base = f"{self.kind.value}|{scope}|{name_key(row.values['full_name'])}|{apart}"
        self.occurrences[base] += 1
        return hashlib.sha256(f"{base}|{self.occurrences[base]}".encode()).hexdigest()

    async def _row(self, row: _Row) -> None:
        v = row.values
        if "full_name" not in v or not name_key(v["full_name"]):
            raise SkipRow("no_name")
        key = self._key(row)
        record = self.records.get(key)
        entities = dict(record.entities) if record else {}
        required = {
            ImportKind.CONSULTATIONS: ("held_on", "no_date"),
            ImportKind.CASES: ("destination", "no_country"),
            ImportKind.QUEUE: ("queued_on", "no_date"),
            ImportKind.ATTEMPTS: ("sent_on", "no_date"),
        }.get(self.kind)
        if required and v.get(required[0]) is None:
            raise SkipRow(required[1])

        # What decides whether the row is imported, before anything is changed.
        contact_id = self._match(row, entities)
        booking_case = None
        if self.kind == ImportKind.BOOKINGS:
            booking_case = self._booking_case(contact_id, entities, v.get("destination"))
            if booking_case is None:
                raise SkipRow("no_case")
        if self.kind == ImportKind.ATTEMPTS:
            assert self.options.country_id is not None
            if self._due(row) is None and self.windows.get(self.options.country_id) is None:
                raise SkipRow("no_window")

        # Parents are written before the records that refer to them: the models have no
        # relationships for the flush to order them by.
        for t, entry_key, entry in row.new_entries:
            self.session.add(entry)
            self.lists[t][entry_key] = entry.id
            self.list_ids[t].add(entry.id)
            row.created[t.value] += 1
        if row.new_entries:
            await self.session.flush()
        contact = self._contact(row, contact_id)
        await self.session.flush()
        entities["contact"] = str(contact.id)
        if note := v.get("contact_note"):
            self._note(row, contact, note)

        if self.kind == ImportKind.CONSULTATIONS:
            self._consultation(row, contact, entities)
        elif booking_case is not None:
            entities["case"] = str(booking_case.id)
            self._booking(row, booking_case, entities)
        else:
            destination = (
                v["destination"] if self.kind == ImportKind.CASES else self.options.country_id
            )
            assert destination is not None
            case = self._case(row, contact, destination, entities)
            await self.session.flush()
            self._finance(row, case, entities)
            if self.kind == ImportKind.ATTEMPTS:
                self._attempt(row, case, entities)
            elif "queued_on" in v:
                self._queue(row, case, entities)
            self._accounts(row, contact, case, entities)

        if record is None:
            record = ImportRow(
                id=uuid.uuid4(),
                tenant_id=self.tenant_id,
                kind=self.kind,
                key=key,
                run_id=self.run_id,
                entities=entities,
            )
            self.session.add(record)
            self.records[key] = record
        elif record.entities != entities or row.created or row.updated:
            # The last run that changed what the row became.
            record.entities = entities
            record.run_id = self.run_id
        if self.kind == ImportKind.CASES:
            self.claimed.add(entities["case"])

    # --- Contacts -----------------------------------------------------------------------

    @staticmethod
    def _person(row: _Row) -> tuple[str | None, str | None]:
        v = row.values
        phone, telegram = v.get("phone"), v.get("telegram")
        if "contacts" in v:
            phone = phone or v["contacts"][0]
            telegram = telegram or v["contacts"][1]
        return phone, telegram

    def _match(self, row: _Row, entities: dict[str, str]) -> uuid.UUID | None:
        """The row's card: the one it became before, else by phone, by Telegram or by a
        unique name — unless that card has another phone or Telegram than the row."""
        cid = _id(entities, "contact")
        if cid in self.contacts:
            return cid
        phone, telegram = self._person(row)
        if phone and phone in self.by_phone:
            return self.by_phone[phone]
        if telegram and telegram.casefold() in self.by_telegram:
            return self.by_telegram[telegram.casefold()]
        same = [
            c
            for c in self.by_name.get(name_key(row.values["full_name"]), [])
            if not (phone and self.contacts[c].phone and self.contacts[c].phone != phone)
            and not (
                telegram
                and self.contacts[c].telegram
                and self.contacts[c].telegram.casefold() != telegram.casefold()
            )
        ]
        return same[0] if len(same) == 1 else None

    def _contact(self, row: _Row, cid: uuid.UUID | None) -> Contact:
        v = row.values
        phone, telegram = self._person(row)
        if cid is None:
            contact = Contact(
                id=uuid.uuid4(),
                tenant_id=self.tenant_id,
                full_name=v["full_name"],
                phone=phone,
                telegram=telegram,
                created_by=self.user_id,
            )
            self.session.add(contact)
            self._index_contact(contact)
            row.created["contacts"] += 1
        else:
            contact = self.contacts[cid]
        changes: dict[str, Any] = {}
        if phone and contact.phone is None:
            if phone in self.by_phone:
                row.warnings.append((self._header("phone", "contacts"), "phone_taken"))
            else:
                changes["phone"] = phone
                self.by_phone[phone] = contact.id
        if telegram and contact.telegram is None:
            changes["telegram"] = telegram
            self.by_telegram.setdefault(telegram.casefold(), contact.id)
        # Fill what the card does not have yet; the card's own data wins.
        for name, value in (
            ("full_name_latin", v.get("full_name_latin")),
            ("email", v.get("email")),
            ("birth_date", v.get("birth_date")),
            ("lead_channel_id", v.get("channel")),
            ("partner_id", v.get("partner")),
        ):
            if getattr(contact, name) is None:
                changes[name] = value
        if _set(contact, changes) and cid is not None:
            row.updated["contacts"] += 1
        return contact

    def _note(self, row: _Row, contact: Contact, body: str) -> None:
        if (contact.id, body) in self.notes:
            return
        self.session.add(
            ContactNote(
                tenant_id=self.tenant_id, contact_id=contact.id, body=body, author_id=self.user_id
            )
        )
        self.notes.add((contact.id, body))
        row.created["notes"] += 1

    # --- Consultations ------------------------------------------------------------------

    def _consultation(self, row: _Row, contact: Contact, entities: dict[str, str]) -> None:
        v = row.values
        held_on: date = v["held_on"]
        mine = self.consultations[contact.id]
        known = _id(entities, "consultation")
        # The one the row became before, or one entered by hand on the same day.
        consultation = next((c for c in mine if c.id == known), None) or next(
            (c for c in mine if c.held_on == held_on), None
        )
        created = consultation is None
        if consultation is None:
            consultation = Consultation(
                id=uuid.uuid4(),
                tenant_id=self.tenant_id,
                contact_id=contact.id,
                held_on=held_on,
                lead_channel_id=contact.lead_channel_id,
                fee_paid=False,
                created_by=self.user_id,
            )
            self.session.add(consultation)
            mine.append(consultation)
            row.created["consultations"] += 1
        changes: dict[str, Any] = {
            "held_on": held_on,
            "decision": v.get("decision"),
            "notes": v.get("consultation_notes"),
            "lead_channel_id": v.get("channel"),
        }
        if consultation.id in self.with_case and consultation.decision == Decision.YES:
            # The client came to work later: «думает» in the table no longer holds.
            changes["decision"] = None
        applicants = v.get("applicants")
        if applicants is not None and applicants not in APPLICANTS:
            row.warnings.append((self._header("applicants"), "out_of_range"))
        elif applicants is not None:
            changes["applicants"] = applicants
        if v.get("destinations"):
            changes["destination_ids"] = v["destinations"]
        # A sum in the fee column means it was paid, unless a «paid» column says no.
        fee, paid = v.get("fee_amount"), v.get("fee_paid")
        if fee is not None and fee[0] > 0:
            changes |= {"fee_amount": fee[0], "fee_currency": fee[1]}
            paid = True if paid is None else paid
        if paid is not None and (fee is not None or consultation.fee_amount is not None):
            changes["fee_paid"] = paid
            if not paid:
                consultation.fee_paid_on = None
            elif consultation.fee_paid_on is None:
                changes["fee_paid_on"] = held_on
        if _set(consultation, changes) and not created:
            row.updated["consultations"] += 1
        entities["consultation"] = str(consultation.id)

    # --- Cases --------------------------------------------------------------------------

    def _case(
        self, row: _Row, contact: Contact, destination: uuid.UUID, entities: dict[str, str]
    ) -> VisaCase:
        v = row.values
        case = self.cases.get(_id(entities, "case"))  # type: ignore[arg-type]
        if case is not None and case.contact_id != contact.id:
            case = None
        if case is None:
            # The contact's case for this country, entered by hand or by another table:
            # open ones first, the newest first, not one another row of this table became.
            candidates = sorted(
                (c for c in self.cases_of[contact.id] if c.destination_id == destination),
                key=lambda c: (c.outcome is not None, -c.number),
            )
            case = next((c for c in candidates if str(c.id) not in self.claimed), None)
        created = case is None
        if case is None:
            case = self._new_case(row, contact, destination)

        changes: dict[str, Any] = {
            "purpose_id": v.get("purpose"),
            "registrar_id": v.get("registrar"),
            "vac_number": v.get("vac_number"),
            "application_email": v.get("application_email"),
            "submission_on": v.get("submission_on"),
        }
        login = v.get("email_login")
        # The mailbox the visa center account is on, when the table has no other.
        if case.application_email is None and not changes["application_email"] and login:
            changes["application_email"] = login if "@" in login else None
        if city := v.get("city"):
            center = self.centers.get((case.destination_id, normalize(city)))
            if center is None:
                self.report.seen_unknown(ValueType.CITY, normalize(city), city)
                row.warnings.append((self._header("city"), "unknown_value"))
            changes["visa_center_id"] = center
        changed = _set(case, changes)
        if (outcome := v.get("outcome")) is not None:
            changed |= self._outcome(row, case, outcome)
        elif case.outcome in (VisaOutcome.VISA, VisaOutcome.REFUSAL):
            changed |= _set(case, {"decided_on": v.get("decided_on")})
        stage = self._stage(case)
        if STAGE_ORDER.index(stage) > STAGE_ORDER.index(case.stage):
            case.stage = stage
            changed = True
        if changed and not created:
            row.updated["cases"] += 1
        entities["case"] = str(case.id)
        return case

    def _new_case(self, row: _Row, contact: Contact, destination: uuid.UUID) -> VisaCase:
        """A case started from the contact's last consultation, if any; a consultation
        that was «thinking» becomes «yes», as when a case is opened from it by hand."""
        v = row.values
        consultation = max(
            (c for c in self.consultations[contact.id] if c.held_on <= self.today),
            key=lambda c: c.held_on,
            default=None,
        )
        case = VisaCase(
            id=uuid.uuid4(),
            tenant_id=self.tenant_id,
            number=self.next_number,
            contact_id=contact.id,
            consultation_id=consultation.id if consultation else None,
            destination_id=destination,
            submission_country_id=self.options.submission_country_id,
            stage=CaseStage.WAITING,
            outcome=None,
            created_by=self.user_id,
        )
        self.next_number += 1
        known = [
            d
            for d in (
                consultation.held_on if consultation else None,
                v.get("queued_on"),
                v.get("sent_on"),
                v.get("submission_on"),
                v.get("decided_on"),
            )
            if d is not None
        ]
        if known:
            # A case from the past counts in the month it started, not the import's.
            case.created_at = _midnight(min(known))
        if consultation is not None and consultation.decision in (None, Decision.THINKING):
            consultation.decision = Decision.YES
        if consultation is not None:
            self.with_case.add(consultation.id)
        self.session.add(case)
        self.cases[case.id] = case
        self.cases_of[contact.id].append(case)
        row.created["cases"] += 1
        return case

    def _outcome(self, row: _Row, case: VisaCase, outcome: Outcome) -> bool:
        """The result from the table; a changed result clears what does not apply to it
        (as app/core/cases.py does in the case panel)."""
        v = row.values
        changed = False
        if case.outcome != outcome.outcome:
            case.outcome = outcome.outcome
            for name, value in cleared_by_outcome(outcome.outcome).items():
                setattr(case, name, value)
            changed = True
        details: dict[str, Any] = {}
        if outcome.outcome == VisaOutcome.VISA:
            details["visa_entries"] = outcome.entries
            start = v.get("visa_valid_from") or case.visa_valid_from
            end = v.get("visa_valid_to") or case.visa_valid_to
            if start and end and end < start:
                row.warnings.append((self._header("visa_valid_to"), "date_order"))
            else:
                details |= {"visa_valid_from": start, "visa_valid_to": end}
        if outcome.outcome in (VisaOutcome.VISA, VisaOutcome.REFUSAL):
            details["after_appeal"] = True if outcome.after_appeal else None
            details["decided_on"] = v.get("decided_on")
        return _set(case, details) or changed

    def _stage(self, case: VisaCase) -> CaseStage:
        """Where a case from the table stands: a result ends it, a submission date in the
        past means it is under review, a date ahead — documents are being collected."""
        if case.outcome is not None:
            return CaseStage.RESULT
        if case.submission_on is not None:
            return CaseStage.REVIEW if case.submission_on <= self.today else CaseStage.DOCUMENTS
        return CaseStage.WAITING

    def _finance(self, row: _Row, case: VisaCase, entities: dict[str, str]) -> None:
        v = row.values
        price, paid = v.get("price"), v.get("paid")
        if price is None and paid is None:
            return
        finance = self.finances.get(case.id)
        counts = row.updated
        if finance is None:
            finance = CaseFinance(case_id=case.id, tenant_id=self.tenant_id)
            self.session.add(finance)
            self.finances[case.id] = finance
            counts = row.created
        if price is not None and finance.price != price[0]:
            finance.price = price[0]
            counts["finances"] += 1
        if paid is None or paid[0] <= 0:
            return
        amount: Decimal = paid[0]
        payment = self.payments.get(_id(entities, "payment"))  # type: ignore[arg-type]
        if payment is not None and payment.case_id != case.id:
            payment = None
        if payment is None:
            # The day of the payment, else of the submission, else of the import.
            payment = CasePayment(
                id=uuid.uuid4(),
                tenant_id=self.tenant_id,
                case_id=case.id,
                amount=amount,
                currency=self.base,
                rate=Decimal("1"),
                amount_base=amount,
                paid_on=v.get("paid_on") or case.submission_on or self.today,
                created_by=self.user_id,
            )
            self.session.add(payment)
            self.payments[payment.id] = payment
            row.created["payments"] += 1
        elif _set(payment, {"amount": amount, "amount_base": amount, "paid_on": v.get("paid_on")}):
            row.updated["payments"] += 1
        entities["payment"] = str(payment.id)

    # --- Queue and windowed applications ------------------------------------------------

    def _queue(self, row: _Row, case: VisaCase, entities: dict[str, str]) -> None:
        v = row.values
        queued_on: date = v["queued_on"]
        entries = self.entries_of[case.id]
        known = _id(entities, "entry")
        entry = (
            next((e for e in entries if e.id == known), None)
            or next((e for e in entries if e.queued_on == queued_on), None)
            or next((e for e in entries if e.removed_on is None), None)
        )
        created = entry is None
        if entry is None:
            entry = WaitingListEntry(
                id=uuid.uuid4(),
                tenant_id=self.tenant_id,
                case_id=case.id,
                queued_on=queued_on,
                created_by=self.user_id,
            )
            self.session.add(entry)
            entries.append(entry)
            row.created["queue_entries"] += 1
        changes: dict[str, Any] = {"queued_on": queued_on}
        for name in ("letter_on", "removed_on"):
            day = v.get(name)
            if day is not None and day < queued_on:
                row.warnings.append((self._header(name), "date_order"))
            elif day is not None:
                changes[name] = day
        # A letter or a removal entered by hand before the new queue date: it stays.
        if any(
            name not in changes and getattr(entry, name) and getattr(entry, name) < queued_on
            for name in ("letter_on", "removed_on")
        ):
            del changes["queued_on"]
            row.warnings.append((self._header("queued_on"), "date_order"))
        if v.get("removed_reason") and (changes.get("removed_on") or entry.removed_on):
            changes["removed_reason"] = v["removed_reason"]
        if _set(entry, changes) and not created:
            row.updated["queue_entries"] += 1
        entities["entry"] = str(entry.id)

    def _due(self, row: _Row) -> date | None:
        """The 60th day from the table, when it is not before the application."""
        due = row.values.get("deadline_on")
        if due is not None and due < row.values["sent_on"]:
            row.warnings.append((self._header("deadline_on"), "date_order"))
            row.values.pop("deadline_on")
            return None
        return due

    def _attempt(self, row: _Row, case: VisaCase, entities: dict[str, str]) -> None:
        v = row.values
        sent_on: date = v["sent_on"]
        attempts = self.attempts_of[case.id]
        known = _id(entities, "attempt")
        attempt = (
            next((a for a in attempts if a.id == known), None)
            or next((a for a in attempts if a.sent_on == sent_on), None)
            or next((a for a in attempts if a.outcome is None), None)
        )
        due = v.get("deadline_on")
        if due is None:
            window = self.windows.get(case.destination_id)
            assert window is not None  # checked before the row changed anything
            due = deadline(sent_on, window)
        taken = {a.number for a in attempts if a is not attempt}
        # «Повторы: 3» — this is the fourth application.
        wanted = v["repeats"] + 1 if v.get("repeats") is not None else None
        if wanted in taken:
            wanted = None
        created = attempt is None
        if attempt is None:
            attempt = ApplicationAttempt(
                id=uuid.uuid4(),
                tenant_id=self.tenant_id,
                case_id=case.id,
                number=wanted or max(taken, default=0) + 1,
                sent_on=sent_on,
                deadline_on=due,
                created_by=self.user_id,
            )
            self.session.add(attempt)
            attempts.append(attempt)
            row.created["attempts"] += 1
        changes: dict[str, Any] = {"sent_on": sent_on, "deadline_on": due, "number": wanted}
        if v.get("submission_on") is not None and attempt.outcome is None:
            changes["outcome"] = AttemptOutcome.DATE_RECEIVED
        if _set(attempt, changes) and not created:
            row.updated["attempts"] += 1
        entities["attempt"] = str(attempt.id)

    # --- Bookings -----------------------------------------------------------------------

    def _booking_case(
        self, contact_id: uuid.UUID | None, entities: dict[str, str], destination: Any
    ) -> VisaCase | None:
        """The case a booking row is for: the one before, else the person's case for the
        row's country (any country if not given), open ones and the newest first."""
        if contact_id is None:
            return None
        case = self.cases.get(_id(entities, "case"))  # type: ignore[arg-type]
        if case is not None and case.contact_id == contact_id:
            return case
        candidates = sorted(
            (
                c
                for c in self.cases_of[contact_id]
                if destination is None or c.destination_id == destination
            ),
            key=lambda c: (c.outcome is not None, -c.number),
        )
        return candidates[0] if candidates else None

    def _booking(self, row: _Row, case: VisaCase, entities: dict[str, str]) -> None:
        v = row.values
        bookings = self.bookings_of[case.id]
        known = _id(entities, "booking")
        booking = next((b for b in bookings if b.id == known), None)
        if booking is None and v.get("reference"):
            booking = next((b for b in bookings if b.reference == v["reference"]), None)
        created = booking is None
        if booking is None:
            booking = Booking(
                id=uuid.uuid4(),
                tenant_id=self.tenant_id,
                case_id=case.id,
                kind=BookingKind.HOTEL,
                place=v.get("place") or v.get("reference") or "Booking",
                created_by=self.user_id,
            )
            self.session.add(booking)
            bookings.append(booking)
            row.created["bookings"] += 1
        changes: dict[str, Any] = {"starts_on": v.get("starts_on"), "ends_on": v.get("ends_on")}
        start = changes["starts_on"] or booking.starts_on
        end = changes["ends_on"] or booking.ends_on
        if start and end and end < start:
            row.warnings.append((self._header("ends_on", "starts_on"), "date_order"))
            changes = {}
        changes |= {
            "place": v.get("place"),
            "free_cancel_until": v.get("free_cancel_until"),
            "reference": v.get("reference"),
            "note": v.get("booking_note"),
        }
        if login := v.get("booking_account"):
            account = self.own_accounts.get(login.casefold())
            if account is None:
                row.warnings.append((self._header("booking_account"), "unknown_value"))
            changes["vault_entry_id"] = account
        if _set(booking, changes) and not created:
            row.updated["bookings"] += 1
        entities["booking"] = str(booking.id)

    # --- Vault --------------------------------------------------------------------------

    def _accounts(
        self, row: _Row, contact: Contact, case: VisaCase, entities: dict[str, str]
    ) -> None:
        for slot, system_id in (
            ("email", self.options.email_system_id),
            ("site", self.options.site_system_id),
        ):
            login = row.values.get(f"{slot}_login")
            password = row.values.get(f"{slot}_password")
            if system_id is None or (login is None and password is None):
                continue
            entry = self.vault.get(_id(entities, f"vault_{slot}"))  # type: ignore[arg-type]
            if entry is not None and entry.contact_id != contact.id:
                entry = None
            if entry is None and login is not None:
                entry = self.accounts.get((contact.id, system_id, login.casefold()))
            if entry is None and login is None:
                row.warnings.append((self._header(f"{slot}_password"), "no_login"))
                continue
            created = entry is None
            if entry is None:
                assert login is not None
                entry = VaultEntry(
                    id=uuid.uuid4(),
                    tenant_id=self.tenant_id,
                    system_id=system_id,
                    contact_id=contact.id,
                    case_id=case.id,
                    login=login,
                    created_by=self.user_id,
                )
                self.session.add(entry)
                self.vault[entry.id] = entry
                self.accounts[(contact.id, system_id, login.casefold())] = entry
                row.created["vault_entries"] += 1
            changed = _set(entry, {"login": login})
            if password is not None and self._set_password(entry, password):
                changed = True
                row.created["passwords"] += 1
            if changed and not created:
                row.updated["vault_entries"] += 1
            entities[f"vault_{slot}"] = str(entry.id)

    def _set_password(self, entry: VaultEntry, password: str) -> bool:
        """Encrypt under the workspace key, bound to the entry (app/core/vault.py)."""
        assert self.vault_key is not None
        if entry.password_encrypted is not None:
            current = decrypt_for_tenant(
                self.vault_key, self.tenant_id, entry.id, entry.password_encrypted
            )
            if current == password:
                return False
        entry.password_encrypted = encrypt_for_tenant(
            self.vault_key, self.tenant_id, entry.id, password
        )
        entry.password_updated_at = datetime.now(UTC)
        return True
