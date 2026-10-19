"""The import of a spreadsheet (backlog 1.14, 1.16, docs/decisions.md → «Импорт таблиц»).

Each row becomes a contact — found by phone, Telegram nick or a unique name, else
created — and the records of the import kind: a consultation, a case with its money, a
queue entry, windowed applications or a booking, with accounts to the vault. The row is
remembered by a key (the kind, the sheet, the person and what tells their rows apart), so
a repeated import updates what the row became instead of adding it again. An empty cell —
or «-», «нет», «0» — never erases data in the CRM.

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
    CellError,
    Outcome,
    cell_text,
    group_names,
    is_blank,
    is_nick,
    is_not_sent,
    is_self_registered,
    name_key,
    normalize,
    parse_bool,
    parse_contacts,
    parse_decision,
    parse_email,
    parse_int,
    parse_loose_date,
    parse_money,
    parse_month,
    parse_outcome,
    parse_phone,
    parse_reference,
    parse_telegram,
    removal_date,
    split_credentials,
    split_list,
    split_name,
)
from app.core.queues import deadline
from app.core.vault import decrypt_for_tenant, encrypt_for_tenant, get_tenant_key
from app.models import (
    ApplicationAttempt,
    Booking,
    BookingKind,
    BookingType,
    CaseFinance,
    CasePayment,
    CaseStage,
    Consultation,
    Contact,
    ContactNote,
    Country,
    CountryRule,
    Decision,
    EmailOwner,
    ImportKind,
    ImportRow,
    LeadChannel,
    Partner,
    PartnerTermsType,
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
VAULT_FIELDS = {
    "email_login",
    "own_email_login",
    "email_password",
    "site_login",
    "site_password",
    "booking_account",
}
# A login cell may hold the password after it; it goes to the account of this slot.
LOGIN_SLOTS = {"email_login": "email", "own_email_login": "own", "site_login": "site"}
MONEY_FIELDS = {"price", "paid", "registrar_fee", "partner_payout"}
# A windowed application and the ones sent after its window ran out: sent, the 60th day.
CYCLES = (
    ("sent_on", "deadline_on"),
    ("sent_on_2", "deadline_on_2"),
    ("sent_on_3", "deadline_on_3"),
    ("sent_on_4", None),
)
# Short names of countries the reference list keeps under another name.
COUNTRY_ALIASES = {
    "uk": "GB",
    "англия": "GB",
    "британия": "GB",
    "usa": "US",
    "сша": "US",
    "америка": "US",
}
# A cell that reads as nothing: «0» in a money column, «я» as the registrar.
_EMPTY = object()
# «не ставим» in a «Повторно» column.
NOT_SENT = "not_sent"


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
    # Passwords typed after a login («почта пароль»), by the account slot.
    passwords: dict[str, str] = field(default_factory=dict)
    # Mailboxes with a password typed after them in an email column: header, login, password.
    mailboxes: list[tuple[str, str, str]] = field(default_factory=list)


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
        sheet: str = "",
        year: int | None = None,
        vault: bool = True,
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
        # The sheet (or the CSV file) the rows come from: Подача25 and Подача26 are apart.
        self.sheet = normalize(sheet)
        # The year the sheet is about: for «17.08» and «Месяц обращения» without a year.
        self.year = year
        self.vault_enabled = vault
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
        by_code: dict[str, uuid.UUID] = {}
        for cid, code, name_ru, name_en in await s.execute(
            select(Country.id, Country.code, Country.name_ru, Country.name_en)
        ):
            countries |= {code.casefold(): cid, normalize(name_ru): cid, normalize(name_en): cid}
            by_code[code] = cid
        for alias, code in COUNTRY_ALIASES.items():
            if code in by_code:
                countries.setdefault(alias, by_code[code])
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
        self.windows: dict[uuid.UUID, int | None] = {}
        self.booking_types: dict[uuid.UUID, BookingType] = {}
        for destination_id, window, booking_type in await s.execute(
            select(
                CountryRule.destination_id, CountryRule.window_days, CountryRule.booking_type
            ).where(CountryRule.submission_id == self.options.submission_country_id)
        ):
            self.windows[destination_id] = window
            self.booking_types[destination_id] = booking_type

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
        if mapped & MONEY_FIELDS:
            for f in await s.scalars(select(CaseFinance), execution_options=FRESH):
                self.finances[f.case_id] = f
        if "paid" in mapped:
            for p in await s.scalars(select(CasePayment), execution_options=FRESH):
                self.payments[p.id] = p

        self.vault: dict[uuid.UUID, VaultEntry] = {}
        self.accounts: dict[tuple[uuid.UUID | None, uuid.UUID, str], VaultEntry] = {}
        self.own_accounts: dict[str, uuid.UUID] = {}
        if self.vault_enabled and mapped & (VAULT_FIELDS | {"email", "application_email"}):
            for v in await s.scalars(select(VaultEntry), execution_options=FRESH):
                self.vault[v.id] = v
                self.accounts[(v.contact_id, v.system_id, v.login.casefold())] = v
                self.own_accounts.setdefault(v.login.casefold(), v.id)

        # Notes the import may add: from a column, after a name, from a queue's comment.
        self.notes: set[tuple[uuid.UUID, str]] = set(
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
        if is_nick(contact.telegram):
            self.by_telegram.setdefault(contact.telegram.casefold(), contact.id)  # type: ignore[union-attr]
        self.by_name[name_key(contact.full_name)].append(contact.id)

    # --- Rows ---------------------------------------------------------------------------

    async def run(self, rows: list[SheetRow]) -> Report:
        await self.load()
        for sheet_row in rows:
            row = self._parse(sheet_row)
            if not row.raw:
                # Nothing in the columns imported: a placeholder row of zeros and dashes.
                continue
            self.report.rows += 1
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
            # «-» and «нет» are an empty cell — except where «нет» is the answer.
            if spec.type not in (ValueType.BOOL, ValueType.DECISION) and is_blank(
                value, words=spec.type != ValueType.RESEND
            ):
                continue
            header = self.headers[index] or f"#{index + 1}"
            try:
                parsed = self._value(spec, cell, value, row, header)
            except ValueError as error:
                reason = error.reason if isinstance(error, CellError) else None
                row.warnings.append((header, reason or f"bad_{spec.type.value}"))
                row.raw[key] = value
                continue
            if parsed is not _EMPTY:
                row.raw[key] = value
                row.values[key] = parsed
        return row

    def _value(self, spec: ImportField, cell: Cell, value: str, row: _Row, header: str) -> Any:
        t = spec.type
        if spec.max_length is not None and len(value) > spec.max_length:
            if not spec.truncate:
                raise CellError("too_long")
            # A long note is cut, not lost.
            row.warnings.append((header, "truncated"))
            value = cell = value[: spec.max_length]
        if t == ValueType.NAME:
            name, note = split_name(value)
            if note:
                row.values["name_note"] = note
            if len(group_names(name)) > 1:
                row.warnings.append((header, "several_people"))
            return name
        if spec.key in LOGIN_SLOTS:
            # «anna@mail.ru Pa55word»: the password goes to the vault, never to a login.
            login, password = split_credentials(value)
            if password:
                row.passwords[LOGIN_SLOTS[spec.key]] = password
            return login
        if spec.key == "booking_account":
            return split_credentials(value)[0]  # looked up by the login only
        if t in (ValueType.TEXT, ValueType.SECRET, ValueType.CITY):
            return value  # a city is looked up with the case's country
        if t == ValueType.REFERENCE:
            return parse_reference(cell)
        if t == ValueType.PHONE:
            return parse_phone(cell)
        if t == ValueType.TELEGRAM:
            return parse_telegram(cell)
        if t == ValueType.CONTACTS:
            return parse_contacts(cell)
        if t == ValueType.EMAIL:
            # «anna@mail.ru Pa55word»: one word after the mailbox is its password.
            words = value.split()
            if len(words) == 2 and "@" in words[0] and not words[1].startswith("("):
                email = parse_email(words[0].rstrip(",;"))
                row.mailboxes.append((header, email, words[1]))
                return email
            return parse_email(cell)
        if t == ValueType.DATE:
            day, guess = parse_loose_date(cell, self.today, self.year)
            if spec.key == "birth_date" and (day.year < 1910 or day > self.today):
                raise CellError("unlikely_birth_date")
            if guess:
                row.warnings.append((header, guess))
            return day
        if t == ValueType.MONTH:
            return parse_month(cell)
        if t == ValueType.RESEND:
            if is_not_sent(value):
                return NOT_SENT
            day, guess = parse_loose_date(cell, self.today, self.year)
            if guess:
                row.warnings.append((header, guess))
            return day
        if t == ValueType.INT:
            return parse_int(cell)
        if t == ValueType.MONEY:
            amount, currency = parse_money(cell, self.options.currency)
            if amount == 0:
                return _EMPTY  # the sheets write «0» for nothing
            if currency != self.base and spec.key != "fee_amount":
                # Case money is kept in the base currency; another one needs a rate.
                row.warnings.append((header, "foreign_currency"))
                return None
            return amount, currency
        if t == ValueType.REGISTRAR and is_self_registered(value):
            return _EMPTY  # the specialist or the client: no registrar, no talon
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

    def _lookup(
        self, t: ValueType, value: str, row: _Row, header: str, alias: bool = False
    ) -> uuid.UUID | None:
        """An id in a list by name. An unknown value is added to the list (the
        workspace's lists) or skipped (countries) unless the specialist chose otherwise:
        an entry of the list, «as» another unknown value (two spellings of one registrar)
        or, for a channel, «a partner» (a partner's name in the «where from» column)."""
        key = normalize(value)
        if key in self.lists[t]:
            return self.lists[t][key]
        if not alias:
            self.report.seen_unknown(t, key, value)
        choice = self.choices.get(t.value, {}).get(key)
        if choice is not None and choice.startswith("as:") and not alias:
            return self._lookup(t, choice[3:], row, header, alias=True)
        if choice == "partner" and t == ValueType.CHANNEL:
            row.values["channel_partner"] = self._lookup(ValueType.PARTNER, value, row, header)
            return None
        if choice not in (None, "new", "skip", "partner") and not choice.startswith("as:"):
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
        """The row's identity across imports: the kind, the sheet, the table's country,
        the person, what tells their rows apart (the case's country) and which such row
        it is. The sheet keeps the years apart: Подача25 and Подача26 have the same people."""
        scope = str(self.options.country_id or "")
        apart = normalize(row.raw.get("destination", "")) if self.kind == ImportKind.CASES else ""
        person = name_key(row.values["full_name"])
        base = f"{self.kind.value}|{self.sheet}|{scope}|{person}|{apart}"
        self.occurrences[base] += 1
        return hashlib.sha256(f"{base}|{self.occurrences[base]}".encode()).hexdigest()

    async def _row(self, row: _Row) -> None:
        v = row.values
        if not v.get("full_name") or not name_key(v["full_name"]):
            raise SkipRow("no_name")
        key = self._key(row)
        record = self.records.get(key)
        entities = dict(record.entities) if record else {}
        if self.kind == ImportKind.CONSULTATIONS and v.get("held_on") is None:
            self._held_in_month(row)
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
            if contact_id is None and len(people := group_names(v["full_name"])) > 1:
                # «Анна и Олег»: the booking is in the case of the first one found.
                contact_id = next((c for p in people if (c := self._by_name(p))), None)
            booking_case = self._booking_case(contact_id, entities, v.get("destination"))
            if booking_case is None:
                raise SkipRow("no_case")
        cycles: list[tuple[date, date | None]] = []
        stopped = False
        if self.kind == ImportKind.ATTEMPTS:
            assert self.options.country_id is not None
            cycles, stopped = self._cycles(row)
            if any(due is None for _, due in cycles) and (
                self.windows.get(self.options.country_id) is None
            ):
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
        for note in (v.get("name_note"), v.get("contact_note")):
            if note:
                self._note(row, contact, note)

        case: VisaCase | None = None
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
            if self.kind == ImportKind.ATTEMPTS:
                self._attempt(row, case, entities, cycles, stopped)
            elif "queued_on" in v:
                if self.kind == ImportKind.QUEUE or (
                    self.booking_types.get(case.destination_id) == BookingType.WAITING_LIST
                ):
                    self._queue(row, contact, case, entities)
                else:
                    # The country has no waiting list: the date is not an entry in one.
                    row.warnings.append((self._header("queued_on"), "not_a_queue"))
            self._finance(row, case, entities)
        await self._accounts(row, contact, case, entities)

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
        """The row's card: the one it became before, else by phone, by Telegram nick or
        by a unique name — unless that card has another phone or nick than the row. A
        display name or a remark in the Telegram column («Анна», «в вайбере») tells
        nobody apart."""
        cid = _id(entities, "contact")
        if cid in self.contacts:
            return cid
        phone, telegram = self._person(row)
        nick = telegram.casefold() if is_nick(telegram) else None  # type: ignore[union-attr]
        if phone and phone in self.by_phone:
            return self.by_phone[phone]
        if nick and nick in self.by_telegram:
            return self.by_telegram[nick]
        same = [
            c
            for c in self.by_name.get(name_key(row.values["full_name"]), [])
            if not (phone and self.contacts[c].phone and self.contacts[c].phone != phone)
            and not (
                nick
                and is_nick(self.contacts[c].telegram)
                and self.contacts[c].telegram.casefold() != nick  # type: ignore[union-attr]
            )
        ]
        return same[0] if len(same) == 1 else None

    def _by_name(self, name: str) -> uuid.UUID | None:
        same = self.by_name.get(name_key(name), [])
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
            if is_nick(telegram):
                self.by_telegram.setdefault(telegram.casefold(), contact.id)
        # Fill what the card does not have yet; the card's own data wins.
        for name, value in (
            ("full_name_latin", v.get("full_name_latin")),
            ("email", v.get("email")),
            ("birth_date", v.get("birth_date")),
            ("lead_channel_id", v.get("channel")),
            ("partner_id", v.get("partner") or v.get("channel_partner")),
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

    def _held_in_month(self, row: _Row) -> None:
        """No day of the consultation (or a typo, «06.04.0205»): the 1st of «Месяц
        обращения» in the sheet's year (else the last such month up to today)."""
        month = row.values.get("held_month")
        if month is None:
            return
        year = self.year or self.today.year
        if self.year is None and date(year, month, 1) > self.today:
            year -= 1
        row.values["held_on"] = date(year, month, 1)
        row.warnings.append((self._header("held_month"), "date_from_month"))

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
        # The mailbox the visa center account is on, when the table has no other: the one
        # the specialist made for the client, else the account's mailbox.
        own, login = v.get("own_email_login"), v.get("email_login")
        if case.application_email is None and not changes["application_email"]:
            if own and "@" in own:
                changes["application_email"] = own
                changes["application_email_owner"] = EmailOwner.SPECIALIST
            elif login and "@" in login:
                changes["application_email"] = login
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
        """A case started from the contact's last consultation before it, if any — a case
        of 2024 is not from a consultation of 2026; a consultation that was «thinking»
        becomes «yes», as when a case is opened from it by hand."""
        v = row.values
        started = min(
            (
                d
                for d in (
                    v.get("queued_on"),
                    v.get("sent_on"),
                    v.get("submission_on"),
                    v.get("decided_on"),
                )
                if d is not None
            ),
            default=self.today,
        )
        consultation = max(
            (c for c in self.consultations[contact.id] if c.held_on <= min(started, self.today)),
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
        fee, payout = v.get("registrar_fee"), v.get("partner_payout")
        if fee is not None and case.registrar_id is None:
            # A talon is paid to the registrar: «-» or «я» in the registrar column.
            row.warnings.append((self._header("registrar_fee"), "talon_without_registrar"))
            fee = None
        if price is None and paid is None and fee is None and payout is None:
            return
        finance = self.finances.get(case.id)
        counts = row.updated
        if finance is None:
            finance = CaseFinance(case_id=case.id, tenant_id=self.tenant_id)
            self.session.add(finance)
            self.finances[case.id] = finance
            counts = row.created
        changed = False
        if price is not None and finance.price != price[0]:
            finance.price = price[0]
            changed = True
        if fee is not None:
            changed |= self._talon(row, case, finance, fee[0], entities)
        if payout is not None:
            changed |= self._payout(case, finance, payout[0], entities)
        if changed:
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

    def _talon(
        self,
        row: _Row,
        case: VisaCase,
        finance: CaseFinance,
        amount: Decimal,
        entities: dict[str, str],
    ) -> bool:
        """The talon from the table, in the base currency. A new one is already paid to
        the registrar (the owner's decision of 18.10.2026): in the profit, not a debt in
        the settlements. A talon entered in the CRM stays as it is."""
        mine = entities.get("talon") == "import"
        if finance.registrar_fee_amount is not None and not mine:
            return False
        entities["talon"] = "import"
        changes: dict[str, Any] = {
            "registrar_fee_amount": amount,
            "registrar_fee_currency": self.base,
            "registrar_fee_rate": Decimal("1"),
            "registrar_fee_base": amount,
        }
        if finance.registrar_fee_on is None:
            changes["registrar_fee_on"] = self._talon_day(row, case)
        if finance.registrar_fee_amount is None:
            changes["registrar_fee_settled"] = True
        return _set(finance, changes)

    def _talon_day(self, row: _Row, case: VisaCase) -> date:
        """As in the case finances: the day the client was queued, else the submission,
        else the import."""
        queued = [e.queued_on for e in self.entries_of[case.id]]
        return (
            row.values.get("queued_on")
            or min(queued, default=None)
            or case.submission_on
            or self.today
        )

    def _payout(
        self, case: VisaCase, finance: CaseFinance, amount: Decimal, entities: dict[str, str]
    ) -> bool:
        """What the partner was paid: a fixed payout, already paid out, like the talons.
        Terms set in the CRM stay as they are."""
        mine = entities.get("payout") == "import"
        if finance.partner_terms is not None and not mine:
            return False
        entities["payout"] = "import"
        return _set(
            finance,
            {
                "partner_terms": PartnerTermsType.FIXED,
                "partner_fixed": amount,
                "partner_paid_on": finance.partner_paid_on
                or case.decided_on
                or case.submission_on
                or self.today,
            },
        )

    # --- Queue and windowed applications ------------------------------------------------

    def _queue(self, row: _Row, contact: Contact, case: VisaCase, entities: dict[str, str]) -> None:
        """The waiting list entry of the row; put in the queue again (Italy's new visa
        center), the first entry ends that day and a second one starts. A comment
        «Удалена из очереди 12.06.2026» ends the entry; any other goes to the card."""
        v = row.values
        queued_on: date = v["queued_on"]
        letter_on, removed_on = v.get("letter_on"), v.get("removed_on")
        reason = v.get("removed_reason")
        if note := v.get("queue_note"):
            header = self._header("queue_note")
            try:
                removal = removal_date(note, self.today, self.year)
            except CellError as error:
                row.warnings.append((header, error.reason))
                removal = None
            if removal is None:
                self._note(row, contact, note)
            else:
                removed_on = removed_on or removal[0]
                reason = reason or note[:500]
                if removal[1]:
                    row.warnings.append((header, removal[1]))
        requeued_on = v.get("requeued_on")
        if requeued_on is not None and requeued_on < queued_on:
            row.warnings.append((self._header("requeued_on"), "date_order"))
            requeued_on = None
        if requeued_on is None:
            self._entry(row, case, entities, "entry", queued_on, letter_on, removed_on, reason)
            return
        self._entry(row, case, entities, "entry", queued_on, None, requeued_on, None)
        self._entry(row, case, entities, "entry_2", requeued_on, letter_on, removed_on, reason)

    def _entry(
        self,
        row: _Row,
        case: VisaCase,
        entities: dict[str, str],
        slot: str,
        queued_on: date,
        letter_on: date | None,
        removed_on: date | None,
        reason: str | None,
    ) -> None:
        entries = self.entries_of[case.id]
        known = _id(entities, slot)
        entry = next((e for e in entries if e.id == known), None) or next(
            (e for e in entries if e.queued_on == queued_on), None
        )
        if entry is None and slot == "entry":
            entry = next((e for e in entries if e.removed_on is None), None)
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
        for name, day in (("letter_on", letter_on), ("removed_on", removed_on)):
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
        if reason and (changes.get("removed_on") or entry.removed_on):
            changes["removed_reason"] = reason
        if _set(entry, changes) and not created:
            row.updated["queue_entries"] += 1
        entities[slot] = str(entry.id)

    def _cycles(self, row: _Row) -> tuple[list[tuple[date, date | None]], bool]:
        """The applications of the row: the first, then each sent again («Повторно»),
        with the 60th day from the table when it is not before the sending. «не ставим»
        ends the chain (True)."""
        v = row.values
        cycles: list[tuple[date, date | None]] = []
        for sent_key, due_key in CYCLES:
            sent = v.get(sent_key)
            if sent == NOT_SENT:
                return cycles, True
            if sent is None:
                break
            if cycles and sent < cycles[-1][0]:
                row.warnings.append((self._header(sent_key), "date_order"))
                break
            due = v.get(due_key) if due_key else None
            if due is not None and due < sent:
                row.warnings.append((self._header(due_key), "date_order"))  # type: ignore[arg-type]
                due = None
            cycles.append((sent, due))
        return cycles, False

    def _attempt(
        self,
        row: _Row,
        case: VisaCase,
        entities: dict[str, str],
        cycles: list[tuple[date, date | None]],
        stopped: bool,
    ) -> None:
        """One application per cycle of the row. One sent again ends the one before as
        expired on its 60th day; the last is open, has a date (a submission day in the
        row) or — «не ставим» — is cancelled."""
        v = row.values
        attempts = self.attempts_of[case.id]
        # «Повторы: 3» — the first application of the row is the fourth.
        before = v["repeats"] if v.get("repeats") is not None else None
        for number, (sent_on, due) in enumerate(cycles, start=1):
            last = number == len(cycles)
            slot = "attempt" if number == 1 else f"attempt_{number}"
            known = _id(entities, slot)
            attempt = next((a for a in attempts if a.id == known), None) or next(
                (a for a in attempts if a.sent_on == sent_on), None
            )
            if attempt is None and last:
                attempt = next((a for a in attempts if a.outcome is None), None)
            if due is None:
                window = self.windows.get(case.destination_id)
                assert window is not None  # checked before the row changed anything
                due = deadline(sent_on, window)
            taken = {a.number for a in attempts if a is not attempt}
            wanted = before + number if before is not None else None
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
            if attempt.outcome is None:
                if not last:
                    changes |= {"outcome": AttemptOutcome.EXPIRED, "closed_on": due}
                elif stopped:
                    closed = min(due, self.today)
                    changes |= {"outcome": AttemptOutcome.CANCELLED, "closed_on": closed}
                elif v.get("submission_on") is not None:
                    changes["outcome"] = AttemptOutcome.DATE_RECEIVED
            if _set(attempt, changes) and not created:
                row.updated["attempts"] += 1
            entities[slot] = str(attempt.id)

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

    def _slots(self, row: _Row) -> list[tuple[str | None, uuid.UUID | None, Any, Any, bool]]:
        """The row's accounts: slot, system, login, password, made by the specialist. A
        mailbox the specialist made is the email account when the table has no other; a
        password typed after a mailbox in an email column makes an account of it too."""
        v, typed = row.values, row.passwords
        email, site = self.options.email_system_id, self.options.site_system_id
        login, own = v.get("email_login"), v.get("own_email_login")
        password = v.get("email_password")
        slots: list[tuple[str | None, uuid.UUID | None, Any, Any, bool]] = []
        if login is None and own is not None:
            slots.append(("email", email, own, password or typed.get("own"), True))
        else:
            slots.append(("email", email, login, password or typed.get("email"), False))
            if own is not None:
                slots.append(("own", email, own, typed.get("own"), True))
        site_password = v.get("site_password") or typed.get("site")
        slots.append(("site", site, v.get("site_login"), site_password, False))
        slots += [(None, email, mailbox, secret, False) for _, mailbox, secret in row.mailboxes]
        return slots

    async def _accounts(
        self, row: _Row, contact: Contact, case: VisaCase | None, entities: dict[str, str]
    ) -> None:
        for slot, system_id, login, password, own in self._slots(row):
            if login is None and password is None:
                continue
            if system_id is None or not self.vault_enabled:
                # Nowhere to keep the password: it is not saved anywhere else.
                if password is not None:
                    keys = (f"{slot}_password", f"{slot}_login") if slot else ("email",)
                    column = self._header(*keys) or self._header("application_email")
                    row.warnings.append((column, "password_not_saved"))
                continue
            entry = self.vault.get(_id(entities, f"vault_{slot}")) if slot else None  # type: ignore[arg-type]
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
                    case_id=case.id if case else None,
                    login=login,
                    created_by=self.user_id,
                )
                self.session.add(entry)
                self.vault[entry.id] = entry
                self.accounts[(contact.id, system_id, login.casefold())] = entry
                row.created["vault_entries"] += 1
            changes: dict[str, Any] = {"login": login}
            if own:
                changes |= {"created_manually": True, "email_owner": EmailOwner.SPECIALIST}
            changed = _set(entry, changes)
            if password is not None and await self._set_password(entry, password):
                changed = True
                row.created["passwords"] += 1
            if changed and not created:
                row.updated["vault_entries"] += 1
            if slot:
                entities[f"vault_{slot}"] = str(entry.id)

    async def _set_password(self, entry: VaultEntry, password: str) -> bool:
        """Encrypt under the workspace key, bound to the entry (app/core/vault.py)."""
        if self.vault_key is None:
            self.vault_key = await get_tenant_key(self.session, self.tenant_id)
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
