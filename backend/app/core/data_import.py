"""The import of a file in the VisaCRM data format (backlog 1.21, docs/data-format.md →
«Повторная загрузка», docs/decisions.md → «Формат данных VisaCRM»).

The sheets go in the order their records depend on each other: clients, notes,
consultations, cases, payments, expenses, the queue, windowed applications, accounts,
bookings. Codes link the sheets of one file. A row finds its record by the CRM's own code
(an export), else by its content — a client by phone, Telegram nick or a unique name, a
case by the client, the country, its source and which such row it is, a payment by its
day, sum and currency — else the record is made. So a file made again by the AI, with other
codes, finds what the last one made.

A file from the AI or made by hand only adds: an empty cell changes nothing, and a client's
card only gets what it does not have yet. A row of an export (its own code is the CRM's and
the file says when it was exported) is a snapshot of its record: its values replace the
record's, an empty cell clears the field, and a record changed in the CRM after the export
is left alone. A password is never cleared by an empty cell.

The workspace's records are loaded once and looked up in memory. Each row runs in its own
savepoint: a row the database refuses is skipped and reported, and the records are loaded
again, since the rollback expired them.

A row in another currency without a rate of its own takes the site's latest National Bank
rate (app/core/rates.py): the import asks the bank nothing. A run reports how far it is in
its Progress, which the page shows while the file loads in the background
(app/core/import_jobs.py), and stops at the next row when asked to.
"""

import hashlib
import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import func, select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.cases import cleared_by_outcome
from app.core.data_format import CREATABLE, ValueType
from app.core.data_values import normalize
from app.core.finance import to_base
from app.core.names import joined, latin_fields, name_fields
from app.core.queues import deadline
from app.core.rates import RateUnavailable, rate_to_base
from app.core.vault import decrypt_for_tenant, encrypt_for_tenant, get_tenant_key
from app.core.workbook import Issue, Row, SheetData, Workbook
from app.models import (
    ApplicationAttempt,
    Booking,
    BookingKind,
    CaseExpense,
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
    SubmissionGroup,
    Tenant,
    VaultEntry,
    VaultSystem,
    VisaCase,
    VisaCenter,
    VisaEntries,
    VisaOutcome,
    VisaPurpose,
    WaitingListEntry,
)
from app.models.booking import BookingStatus
from app.models.contact import Messenger
from app.models.dictionaries import Hall
from app.models.queue import AttemptOutcome

# Sheets in the order their records depend on each other.
SHEET_ORDER = (
    "clients",
    "notes",
    "consultations",
    "cases",
    "payments",
    "expenses",
    "queue",
    "attempts",
    "accounts",
    "bookings",
)
# The report keeps this many skipped rows and warnings; the counts are always whole.
REPORT_LIMIT = 1000
# Where the first specialist's clients apply: the default country of application.
DEFAULT_SUBMISSION = "BY"
# Applicants in one consultation (the consultations table check).
APPLICANTS = range(1, 31)
STAY_DAYS = range(1, 3661)
BIRTH_YEARS_FROM = 1910
STAGE_ORDER = list(CaseStage)
FRESH = {"populate_existing": True}
# The passport page on the card's questionnaire (app/core/questionnaire.py → Passport).
PASSPORT = (
    "passport_number",
    "personal_number",
    "nationality",
    "sex",
    "passport_issued_on",
    "passport_expires_on",
    "passport_issued_by",
    "birth_place",
)
# A choice for an unknown list value: add it, leave the cell empty, or an entry's id.
NEW, SKIP = "new", "skip"
# Sheets whose codes other sheets point to.
_CODED = {"clients", "consultations", "cases"}


class ImportStopped(Exception):
    """The run was asked to stop (a newer check replaced it); nothing it did is kept."""


@dataclass
class Progress:
    """How far a run is: the rows of the file gone through."""

    rows_done: int = 0
    rows_total: int = 0
    # Set from outside: the run stops before its next row.
    stop: bool = False

    def check(self) -> None:
        if self.stop:
            raise ImportStopped


class SkipRow(Exception):
    def __init__(self, reason: str, column: str | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.column = column


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

    def skip(self, issue: Issue) -> None:
        self.skipped_count += 1
        if len(self.skipped) < REPORT_LIMIT:
            self.skipped.append(_issue(issue))

    def warn(self, issue: Issue) -> None:
        self.warnings_count += 1
        if len(self.warnings) < REPORT_LIMIT:
            self.warnings.append(_issue(issue))

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


def _issue(issue: Issue) -> dict[str, Any]:
    return {"sheet": issue.sheet, "row": issue.row, "column": issue.column, "reason": issue.reason}


@dataclass
class _Row:
    """One row in progress: the sheet, its values, what it changed."""

    sheet: SheetData
    row: Row
    snapshot: bool = False
    warnings: list[Issue] = field(default_factory=list)
    created: Counter[str] = field(default_factory=Counter)
    updated: Counter[str] = field(default_factory=Counter)
    # List entries the row adds: indexed only once the row is imported.
    new_entries: list[tuple[ValueType, str, Any]] = field(default_factory=list)
    # Something was written: a failure after it reloads the records.
    wrote: bool = False

    @property
    def values(self) -> dict[str, Any]:
        return self.row.values

    def header(self, key: str) -> str | None:
        return self.sheet.headers.get(key)

    def warn(self, key: str | None, reason: str) -> None:
        column = self.header(key) if key else None
        self.warnings.append(Issue(reason, self.sheet.title, self.row.number, column))

    def blank(self, key: str) -> bool:
        """The cell is in the file and empty: a snapshot row clears its field."""
        return self.snapshot and key in self.row.blank


def name_key(value: str) -> str:
    """A person's name for matching: «Иванова Анна» and «анна иванова» are one."""
    return " ".join(sorted(normalize(value).split()))


def _midnight(day: date) -> datetime:
    return datetime.combine(day, time.min, UTC)


def _by_case(records: Any) -> defaultdict[uuid.UUID, list[Any]]:
    of: defaultdict[uuid.UUID, list[Any]] = defaultdict(list)
    for record in records:
        of[record.case_id].append(record)
    return of


def _uuid(code: str | None) -> uuid.UUID | None:
    if code is None:
        return None
    try:
        return uuid.UUID(code)
    except ValueError:
        return None


class WorkbookImport:
    """One run of the import over a parsed workbook (app/core/workbook.py)."""

    def __init__(
        self,
        session: AsyncSession,
        *,
        tenant: Tenant,
        user_id: uuid.UUID,
        modules: list[str],
        choices: dict[str, dict[str, str]],
        today: date,
        run_id: uuid.UUID,
        progress: Progress | None = None,
    ) -> None:
        self.session = session
        self.progress = progress or Progress()
        self.tenant_id = tenant.id
        self.base = tenant.base_currency
        self.zone = ZoneInfo(tenant.timezone)
        self.user_id = user_id
        self.modules = set(modules)
        self.choices = choices
        self.today = today
        self.run_id = run_id
        self.report = Report()
        self.locked = False
        self.vault_key: bytes | None = None
        self.exported_at: datetime | None = None
        # The file's codes, by sheet: what each row became.
        self.codes: defaultdict[str, dict[str, uuid.UUID]] = defaultdict(dict)
        # Records made in this run: their changes later in the run are not «updated».
        self.made: set[uuid.UUID] = set()
        # Cases a row of the file became: not adopted by another row.
        self.claimed: set[uuid.UUID] = set()
        # Cases made in this run without «Дата открытия»: they start at their earliest date.
        self.dated: set[uuid.UUID] = set()
        self.families: defaultdict[str, list[uuid.UUID]] = defaultdict(list)
        self.ungrouped: set[uuid.UUID] = set()
        self.occurrences: Counter[str] = Counter()
        # List entries the file added.
        self.added: set[uuid.UUID] = set()

    # --- Loading ------------------------------------------------------------------------

    async def load(self) -> None:
        """The workspace's records the import looks up, by the keys it looks them up by."""
        s = self.session
        self.contacts: dict[uuid.UUID, Contact] = {}
        self.by_phone: dict[str, uuid.UUID] = {}
        self.by_telegram: dict[str, uuid.UUID] = {}
        self.by_name: defaultdict[str, list[uuid.UUID]] = defaultdict(list)
        for contact in await s.scalars(select(Contact), execution_options=FRESH):
            self._index_contact(contact)
        self.notes: set[tuple[uuid.UUID, str]] = set(
            (await s.execute(select(ContactNote.contact_id, ContactNote.body))).all()
        )

        self.lists: dict[ValueType, dict[str, uuid.UUID]] = {}
        for t, model in (
            (ValueType.CHANNEL, LeadChannel),
            (ValueType.PARTNER, Partner),
            (ValueType.PURPOSE, VisaPurpose),
            (ValueType.REGISTRAR, Registrar),
        ):
            rows = await s.execute(select(model.id, model.name))
            self.lists[t] = {normalize(name): item_id for item_id, name in rows}
        self.lists[ValueType.SYSTEM] = {
            normalize(name): item_id
            for item_id, name in await s.execute(
                select(VaultSystem.id, VaultSystem.name).where(VaultSystem.is_active)
            )
        }
        countries: dict[str, uuid.UUID] = {}
        self.country_codes: dict[uuid.UUID, str] = {}
        for cid, code, name_ru, name_en in await s.execute(
            select(Country.id, Country.code, Country.name_ru, Country.name_en)
        ):
            countries |= {code.casefold(): cid, normalize(name_ru): cid, normalize(name_en): cid}
            self.country_codes[cid] = code
        self.lists[ValueType.COUNTRY] = countries
        self.list_ids = {t: set(index.values()) for t, index in self.lists.items()}

        # Cities and windows of the workspace's directions «destination × where it is
        # submitted». A switched-off city still names old cases.
        self.centers: dict[tuple[uuid.UUID, uuid.UUID, str], uuid.UUID] = {}
        for center_id, destination, submission, city_ru, city_en in await s.execute(
            select(
                VisaCenter.id,
                CountryRule.destination_id,
                CountryRule.submission_id,
                VisaCenter.city_ru,
                VisaCenter.city_en,
            ).join(CountryRule, VisaCenter.rule_id == CountryRule.id)
        ):
            for city in filter(None, (city_ru, city_en)):  # the English name is optional
                self.centers[(destination, submission, normalize(city))] = center_id
        self.windows: dict[tuple[uuid.UUID, uuid.UUID], int | None] = {
            (destination, submission): window
            for destination, submission, window in await s.execute(
                select(
                    CountryRule.destination_id, CountryRule.submission_id, CountryRule.window_days
                )
            )
        }

        self.consultations: dict[uuid.UUID, Consultation] = {}
        self.consultations_of: defaultdict[uuid.UUID, list[Consultation]] = defaultdict(list)
        for c in await s.scalars(select(Consultation), execution_options=FRESH):
            self.consultations[c.id] = c
            self.consultations_of[c.contact_id].append(c)
        self.cases: dict[uuid.UUID, VisaCase] = {}
        self.cases_of: defaultdict[uuid.UUID, list[VisaCase]] = defaultdict(list)
        for case in await s.scalars(select(VisaCase), execution_options=FRESH):
            self.cases[case.id] = case
            self.cases_of[case.contact_id].append(case)
        self.with_case = {c.consultation_id for c in self.cases.values() if c.consultation_id}
        self.finances: dict[uuid.UUID, CaseFinance] = {
            f.case_id: f for f in await s.scalars(select(CaseFinance), execution_options=FRESH)
        }
        self.payments: dict[uuid.UUID, CasePayment] = {
            p.id: p for p in await s.scalars(select(CasePayment), execution_options=FRESH)
        }
        self.expenses: dict[uuid.UUID, CaseExpense] = {
            e.id: e for e in await s.scalars(select(CaseExpense), execution_options=FRESH)
        }
        self.entries: dict[uuid.UUID, WaitingListEntry] = {
            e.id: e for e in await s.scalars(select(WaitingListEntry), execution_options=FRESH)
        }
        self.attempts: dict[uuid.UUID, ApplicationAttempt] = {
            a.id: a for a in await s.scalars(select(ApplicationAttempt), execution_options=FRESH)
        }
        self.bookings: dict[uuid.UUID, Booking] = {
            b.id: b for b in await s.scalars(select(Booking), execution_options=FRESH)
        }
        # A case's records, looked up by the case: a file has thousands of rows.
        self.payments_of = _by_case(self.payments.values())
        self.expenses_of = _by_case(self.expenses.values())
        self.entries_of = _by_case(self.entries.values())
        self.attempts_of = _by_case(self.attempts.values())
        self.bookings_of = _by_case(self.bookings.values())
        self.vault: dict[uuid.UUID, VaultEntry] = {}
        self.accounts: dict[tuple[uuid.UUID | None, uuid.UUID, str], uuid.UUID] = {}
        self.logins: dict[str, uuid.UUID] = {}
        if "vault" in self.modules:
            for v in await s.scalars(select(VaultEntry), execution_options=FRESH):
                self._index_account(v)
        self.memory: dict[str, ImportRow] = {
            r.key: r
            for r in await s.scalars(
                select(ImportRow).where(ImportRow.kind == ImportKind.WORKBOOK),
                execution_options=FRESH,
            )
        }
        self.claimed |= {
            uuid.UUID(r.entities["case"]) for r in self.memory.values() if "case" in r.entities
        }
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

    def _index_account(self, entry: VaultEntry) -> None:
        self.vault[entry.id] = entry
        login = entry.login.casefold()
        self.accounts[(entry.contact_id, entry.system_id, login)] = entry.id
        # A booking names its account by the login: the specialist's own ones first.
        if entry.contact_id is None or login not in self.logins:
            self.logins[login] = entry.id

    # --- Running ------------------------------------------------------------------------

    async def run(self, book: Workbook) -> Report:
        if book.exported_at is not None:
            self.exported_at = book.exported_at.replace(tzinfo=self.zone).astimezone(UTC)
        for issue in book.skipped:
            self.report.skip(issue)
            if issue.row is not None:
                self.report.rows += 1
        for issue in book.warnings:
            self.report.warn(issue)
        await self.load()
        progress = self.progress
        progress.rows_total = sum(len(sheet.rows) for sheet in book.sheets.values())
        for key in SHEET_ORDER:
            sheet = book.sheets.get(key)
            if sheet is None:
                continue
            if sheet.spec.module not in self.modules:
                self.report.rows += len(sheet.rows)
                self.report.skip(Issue("module_disabled", sheet.title))
                progress.rows_done += len(sheet.rows)
                continue
            self._drop_disabled_columns(sheet)
            handler = getattr(self, f"_{key}")
            for row in sheet.rows:
                progress.check()
                await self._row(sheet, row, handler)
                progress.rows_done += 1
            if key == "cases":
                await self._families(sheet.title)
        return self.report

    def _drop_disabled_columns(self, sheet: SheetData) -> None:
        """A column of a module the workspace has not is passed by, with a warning."""
        off = [
            c.key
            for c in sheet.spec.columns
            if c.module and c.module not in self.modules and c.key in sheet.headers
        ]
        for key in off:
            self.report.warn(Issue("module_disabled", sheet.title, column=sheet.headers.pop(key)))
            for row in sheet.rows:
                row.values.pop(key, None)
                row.blank.discard(key)

    async def _row(self, sheet: SheetData, row: Row, handler: Any) -> None:
        self.report.rows += 1
        r = _Row(sheet, row)
        try:
            async with self.session.begin_nested():
                await handler(r)
                await self.session.flush()
        except SkipRow as skip:
            column = r.header(skip.column) if skip.column else None
            self.report.skip(Issue(skip.reason, sheet.title, row.number, column))
            if r.wrote:
                await self.load()
            return
        except SQLAlchemyError:
            # The database refused the row (a rule the checks here do not know).
            self.report.skip(Issue("row_error", sheet.title, row.number))
            await self.load()
            return
        for t, key, entry in r.new_entries:
            self.lists[t][key] = entry.id
            self.list_ids[t].add(entry.id)
            self.added.add(entry.id)
        self.report.imported += 1
        self.report.created.update(r.created)
        self.report.updated.update(r.updated)
        for issue in r.warnings:
            self.report.warn(issue)
        if (code := row.values.get("code")) is not None and r.sheet.spec.key in _CODED:
            self.codes[r.sheet.spec.key][code] = self._made_id

    # --- Shared steps -------------------------------------------------------------------

    def _write(self, r: _Row, obj: Any, counter: str) -> None:
        """A new record of the row."""
        r.wrote = True
        self.session.add(obj)
        self.made.add(obj.id)
        r.created[counter] += 1

    def _changed(self, r: _Row, obj: Any, counter: str, changed: bool) -> None:
        if changed and obj.id not in self.made:
            r.updated[counter] += 1

    def _own(self, r: _Row, records: dict[uuid.UUID, Any]) -> Any:
        """The record of the row's own code when it is the CRM's (a row of an export); a
        record changed in the CRM after the export is not overwritten by it."""
        record = records.get(_uuid(r.values.get("code")))  # type: ignore[arg-type]
        if record is None:
            return None
        if self.exported_at is not None:
            r.snapshot = True
            changed = [getattr(record, "updated_at", None)]
            if isinstance(record, VisaCase) and record.id in self.finances:
                changed.append(self.finances[record.id].updated_at)
            if any(at is not None and at > self.exported_at for at in changed):
                raise SkipRow("changed_after_export")
        return record

    def _resolve(self, sheet: str, code: str, records: dict[uuid.UUID, Any]) -> Any:
        """A record by a code: the file's own (a row above made it) or the CRM's."""
        if code in self.codes[sheet]:
            return records.get(self.codes[sheet][code])
        return records.get(_uuid(code))  # type: ignore[arg-type]

    def _client_of(self, r: _Row, *, required: bool = True) -> Contact | None:
        code = r.values.get("client")
        if code is None:
            if required:
                raise SkipRow("unknown_code", "client")
            return None
        contact = self._resolve("clients", code, self.contacts)
        if contact is None:
            raise SkipRow("unknown_code", "client")
        self._same_person(r, contact)
        return contact

    def _case_of(self, r: _Row) -> VisaCase:
        case = self._resolve("cases", r.values["case"], self.cases)
        if case is None:
            raise SkipRow("unknown_code", "case")
        self._same_person(r, self.contacts[case.contact_id])
        return case

    @staticmethod
    def _same_person(r: _Row, contact: Contact) -> None:
        """«Клиент» next to a code is the code's person: a shifted code is caught."""
        name = r.values.get("client_name")
        if name is not None and name_key(name) != name_key(contact.full_name):
            raise SkipRow("client_mismatch", "client_name")

    def _assign(self, r: _Row, obj: Any, fields: dict[str, tuple[str, Any]]) -> bool:
        """Set the given values; a snapshot row also clears the fields of its empty cells.
        Fields: attribute → (column key, value or None)."""
        changed = False
        for attr, (key, value) in fields.items():
            if value is None:
                if r.blank(key) and getattr(obj, attr) is not None:
                    setattr(obj, attr, None)
                    changed = True
                continue
            if getattr(obj, attr) != value:
                setattr(obj, attr, value)
                changed = True
        return changed

    def _lookup(self, r: _Row, t: ValueType, key: str) -> uuid.UUID | None:
        """An id in a list by name. An unknown value is added to the workspace's list
        unless the specialist chose otherwise (an entry of the list, or none); a country,
        a city or a system is only looked up."""
        value = r.values.get(key)
        if value is None:
            return None
        found = self._find(t, value)
        name = normalize(value)
        if found is not None:
            if found in self.added:
                # Added by an earlier row of this file: still an unknown value of it.
                self.report.seen_unknown(t, name, value)
            return found
        self.report.seen_unknown(t, name, value)
        choice = self.choices.get(t.value, {}).get(name, NEW)
        if (chosen := _uuid(choice)) is not None and chosen in self.list_ids.get(t, set()):
            return chosen
        if choice != NEW or t not in CREATABLE:
            r.warn(key, "unknown_value")
            return None
        for new_t, new_key, entry in r.new_entries:
            if (new_t, new_key) == (t, name):
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
        r.new_entries.append((t, name, entry))
        self._write(r, entry, t.value)
        return entry.id

    async def _parents(self, r: _Row) -> None:
        """List entries the row adds are written before the records that refer to them:
        the models have no relationships for the flush to order them by."""
        if r.new_entries:
            await self.session.flush()

    def _find(self, t: ValueType, value: str) -> uuid.UUID | None:
        return self.lists[t].get(normalize(value)) or self.lists[t].get(value.casefold())

    def _country(self, r: _Row, key: str) -> uuid.UUID | None:
        value = r.values.get(key)
        if value is None:
            return None
        found = self._find(ValueType.COUNTRY, value)
        if found is None:
            self.report.seen_unknown(ValueType.COUNTRY, normalize(value), value)
            r.warn(key, "unknown_value")
        return found

    async def _rate(self, r: _Row, currency: str, given: Decimal | None) -> Decimal:
        if currency == self.base:
            return Decimal("1")
        if given is not None:
            return given
        try:
            return await rate_to_base(currency, self.base)
        except RateUnavailable as error:
            raise SkipRow("rate_unavailable") from error

    def _starts(self, case: VisaCase, day: date | None) -> None:
        """A case made in this run without «Дата открытия» starts at its earliest date."""
        if day is not None and case.id in self.dated and _midnight(day) < case.created_at:
            case.created_at = _midnight(day)

    # --- Clients ------------------------------------------------------------------------

    def _match(self, v: dict[str, Any]) -> uuid.UUID | None:
        """The card of a person: by phone, by Telegram nick or by a unique name — unless
        that card has another phone or nick than the row."""
        phone, nick = v.get("phone"), (v.get("telegram") or "").casefold() or None
        if phone and phone in self.by_phone:
            return self.by_phone[phone]
        if nick and nick in self.by_telegram:
            return self.by_telegram[nick]
        name = joined(v.get("last_name"), v.get("first_name"), v.get("middle_name"))
        if name is None:
            return None
        same = [
            c
            for c in self.by_name.get(name_key(name), [])
            if not (phone and self.contacts[c].phone and self.contacts[c].phone != phone)
            and not (
                nick and self.contacts[c].telegram and self.contacts[c].telegram.casefold() != nick  # type: ignore[union-attr]
            )
        ]
        return same[0] if len(same) == 1 else None

    async def _clients(self, r: _Row) -> None:
        v = r.values
        contact: Contact | None = self._own(r, self.contacts)
        if contact is None and (cid := self._match(v)) is not None:
            contact = self.contacts[cid]
        if contact is None and not (v.get("last_name") or v.get("first_name")):
            raise SkipRow("no_name")
        channel = self._lookup(r, ValueType.CHANNEL, "channel")
        partner = self._lookup(r, ValueType.PARTNER, "partner")
        await self._parents(r)

        made = contact is None
        if contact is None:
            contact = Contact(
                id=uuid.uuid4(),
                tenant_id=self.tenant_id,
                **name_fields(v.get("last_name"), v.get("first_name"), v.get("middle_name")),
                created_by=self.user_id,
            )
            self._write(r, contact, "contacts")
            self._index_contact(contact)
        r.wrote = True
        changed = False
        if r.snapshot:
            changed |= self._names(r, contact)
        fields: dict[str, tuple[str, Any]] = {
            "telegram": ("telegram", v.get("telegram")),
            "messenger": ("messenger", Messenger(v["messenger"]) if "messenger" in v else None),
            "messenger_contact": ("messenger_contact", v.get("messenger_contact")),
            "email": ("email", v.get("email")),
            "birth_date": ("birth_date", self._birth_date(r)),
            "lead_channel_id": ("channel", channel),
            "partner_id": ("partner", partner),
            "consent_signed_on": ("consent_signed_on", v.get("consent_signed_on")),
            "contract_number": ("contract_number", v.get("contract_number")),
            "contract_signed_on": ("contract_signed_on", v.get("contract_signed_on")),
        }
        if (phone := v.get("phone")) is not None and self.by_phone.get(phone, contact.id) != (
            contact.id
        ):
            r.warn("phone", "phone_taken")
            phone = None
        fields["phone"] = ("phone", phone)
        if not r.snapshot and not made:
            # A file from the AI only fills what the card does not have yet.
            fields = {a: f for a, f in fields.items() if getattr(contact, a) is None}
        changed |= self._assign(r, contact, fields)
        if contact.phone:
            self.by_phone[contact.phone] = contact.id
        if contact.telegram:
            self.by_telegram.setdefault(contact.telegram.casefold(), contact.id)
        changed |= self._latin(r, contact, made)
        changed |= self._passport(r, contact, made)
        if not made:
            self._changed(r, contact, "contacts", changed)
        self._made_id = contact.id

    def _names(self, r: _Row, contact: Contact) -> bool:
        """A snapshot row's name; a card is never left without a surname or a given name."""
        v, parts = r.values, {}
        for key in ("last_name", "first_name", "middle_name"):
            parts[key] = v.get(key) or (None if r.blank(key) else getattr(contact, key))
        if not (parts["last_name"] or parts["first_name"]):
            r.warn("last_name", "no_name")
            return False
        names = name_fields(parts["last_name"], parts["first_name"], parts["middle_name"])
        if all(getattr(contact, k) == value for k, value in names.items()):
            return False
        old = name_key(contact.full_name)
        for k, value in names.items():
            setattr(contact, k, value)
        if contact.id in self.by_name.get(old, []):
            self.by_name[old].remove(contact.id)
        self.by_name[name_key(contact.full_name)].append(contact.id)
        return True

    def _latin(self, r: _Row, contact: Contact, made: bool) -> bool:
        v = r.values
        last, first = v.get("last_name_latin"), v.get("first_name_latin")
        if r.snapshot:
            last = last or (None if r.blank("last_name_latin") else contact.last_name_latin)
            first = first or (None if r.blank("first_name_latin") else contact.first_name_latin)
        elif not made and contact.full_name_latin is not None or last is None and first is None:
            return False
        names = latin_fields(last, first)
        if all(getattr(contact, k) == value for k, value in names.items()):
            return False
        for k, value in names.items():
            setattr(contact, k, value)
        return True

    def _birth_date(self, r: _Row) -> date | None:
        born = r.values.get("birth_date")
        if born is not None and not (date(BIRTH_YEARS_FROM, 1, 1) <= born <= self.today):
            r.warn("birth_date", "out_of_range")
            return None
        return born

    def _passport(self, r: _Row, contact: Contact, made: bool) -> bool:
        """The passport page: kept on the card's questionnaire, as the card form keeps it."""
        v, current = r.values, dict(contact.questionnaire or {})
        updated = dict(current)
        for key in PASSPORT:
            value = v.get(key)
            if key == "nationality" and value is not None:
                country = self._country(r, key)
                value = self.country_codes[country] if country else None
            if isinstance(value, date):
                value = value.isoformat()
            if value is None:
                if r.blank(key):
                    updated.pop(key, None)
                continue
            if not r.snapshot and not made and current.get(key) is not None:
                continue
            updated[key] = value
        if updated == current:
            return False
        contact.questionnaire = updated
        return True

    async def _notes(self, r: _Row) -> None:
        contact = self._client_of(r)
        assert contact is not None
        body = r.values["body"]
        self._made_id = contact.id
        if (contact.id, body) in self.notes:
            return
        note = ContactNote(
            id=uuid.uuid4(),
            tenant_id=self.tenant_id,
            contact_id=contact.id,
            body=body,
            author_id=self.user_id,
        )
        if (written := r.values.get("written_on")) is not None:
            note.created_at = _midnight(written)
        self._write(r, note, "notes")
        self.notes.add((contact.id, body))

    # --- Consultations ------------------------------------------------------------------

    async def _consultations(self, r: _Row) -> None:
        v = r.values
        contact = self._client_of(r)
        assert contact is not None
        consultation: Consultation | None = self._own(r, self.consultations)
        if consultation is not None and consultation.contact_id != contact.id:
            raise SkipRow("code_mismatch", "client")
        held_on: date = v["held_on"]
        mine = self.consultations_of[contact.id]
        if consultation is None:
            consultation = next((c for c in mine if c.held_on == held_on), None)
        destinations = []
        for name in v.get("destinations", []):
            if (found := self._find(ValueType.COUNTRY, name)) is not None:
                destinations.append(found)
            else:
                self.report.seen_unknown(ValueType.COUNTRY, normalize(name), name)
                r.warn("destinations", "unknown_value")
        channel = self._lookup(r, ValueType.CHANNEL, "channel")
        await self._parents(r)
        made = consultation is None
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
            self._write(r, consultation, "consultations")
            self.consultations[consultation.id] = consultation
            mine.append(consultation)
        r.wrote = True
        applicants = v.get("applicants")
        if applicants is not None and applicants not in APPLICANTS:
            r.warn("applicants", "out_of_range")
            applicants = None
        decision = Decision(v["decision"]) if "decision" in v else None
        if consultation.id in self.with_case and consultation.decision == Decision.YES:
            # The client came to work: «думает» in the file no longer holds.
            decision = None
        fee = v.get("fee_amount")
        currency = v.get("fee_currency") or (self.base if fee is not None else None)
        changed = self._assign(
            r,
            consultation,
            {
                "held_on": ("held_on", held_on),
                "decision": ("decision", decision),
                "lead_channel_id": ("channel", channel),
                "trip_planned_on": ("trip_planned_on", v.get("trip_planned_on")),
                "follow_up_on": ("follow_up_on", v.get("follow_up_on")),
                "fee_amount": ("fee_amount", fee),
                "fee_currency": ("fee_amount", currency),
                "fee_paid_on": ("fee_paid_on", v.get("fee_paid_on")),
                "notes": ("notes", v.get("notes")),
            },
        )
        if applicants is not None and consultation.applicants != applicants:
            consultation.applicants = applicants
            changed = True
        if destinations and consultation.destination_ids != destinations:
            consultation.destination_ids = destinations
            changed = True
        paid = v.get("fee_paid")
        if paid is None and r.blank("fee_paid"):
            paid = False
        if paid is not None and consultation.fee_paid != paid:
            consultation.fee_paid = paid
            changed = True
        if consultation.fee_paid and consultation.fee_paid_on is None:
            consultation.fee_paid_on = held_on
        elif not consultation.fee_paid and consultation.fee_paid_on is not None:
            consultation.fee_paid_on = None
            changed = True
        if not made:
            self._changed(r, consultation, "consultations", changed)
        self._made_id = consultation.id

    # --- Cases --------------------------------------------------------------------------

    def _memory_key(self, r: _Row, contact: Contact, destination: uuid.UUID) -> str:
        """A case row's identity across files: the person, the country, its source and
        which such row it is. The source keeps the years apart: Подача25 and Подача26."""
        source = normalize(r.values.get("source") or "")
        base = f"cases|{contact.id}|{destination}|{source}"
        self.occurrences[base] += 1
        return hashlib.sha256(f"{base}|{self.occurrences[base]}".encode()).hexdigest()

    async def _cases(self, r: _Row) -> None:
        v = r.values
        contact = self._client_of(r)
        assert contact is not None
        destination = self._country(r, "destination")
        if destination is None:
            raise SkipRow("unknown_country", "destination")
        case: VisaCase | None = self._own(r, self.cases)
        if case is not None and case.contact_id != contact.id:
            raise SkipRow("code_mismatch", "client")
        key = None
        if case is None:
            key = self._memory_key(r, contact, destination)
            remembered = self.memory.get(key)
            case = self.cases.get(_uuid(remembered.entities.get("case")) if remembered else None)  # type: ignore[arg-type]
            if case is not None and case.contact_id != contact.id:
                case = None
        if case is None:
            # The person's case for this country entered by hand: open ones first, the
            # newest first, not one another row became.
            candidates = sorted(
                (c for c in self.cases_of[contact.id] if c.destination_id == destination),
                key=lambda c: (c.outcome is not None, -c.number),
            )
            case = next((c for c in candidates if c.id not in self.claimed), None)
        submission = self._country(r, "submission_country") if "submission_country" in v else None
        submission = submission or (case.submission_country_id if case else None)
        submission = submission or self._find(ValueType.COUNTRY, DEFAULT_SUBMISSION)
        assert submission is not None
        consultation = self._consultation_of(r)
        purpose = self._lookup(r, ValueType.PURPOSE, "purpose")
        registrar = self._lookup(r, ValueType.REGISTRAR, "registrar")
        center = None
        if (city := v.get("city")) is not None:
            center = self.centers.get((destination, submission, normalize(city)))
            if center is None:
                self.report.seen_unknown(ValueType.CITY, normalize(city), city)
                r.warn("city", "unknown_value")
        await self._parents(r)

        made = case is None
        if case is None:
            case = self._new_case(r, contact, destination, submission, consultation)
        r.wrote = True
        self.claimed.add(case.id)
        changed = self._assign(
            r,
            case,
            {
                "purpose_id": ("purpose", purpose),
                "visa_center_id": ("city", center),
                "hall": ("hall", Hall(v["hall"]) if "hall" in v else None),
                "registrar_id": ("registrar", registrar),
                "vac_number": ("vac_number", v.get("vac_number")),
                "application_email": ("application_email", v.get("application_email")),
                "application_email_owner": (
                    "application_email_owner",
                    EmailOwner(v["application_email_owner"])
                    if "application_email_owner" in v
                    else None,
                ),
                "questionnaire_due_on": ("questionnaire_due_on", v.get("questionnaire_due_on")),
                "questionnaire_received_on": (
                    "questionnaire_received_on",
                    v.get("questionnaire_received_on"),
                ),
                "submission_on": ("submission_on", v.get("submission_on")),
            },
        )
        if not made and "submission_country" in v and case.submission_country_id != submission:
            case.submission_country_id = submission
            changed = True
        changed |= self._outcome(r, case)
        changed |= self._stage(r, case)
        if (opened := v.get("opened_on")) is not None and case.created_at != _midnight(opened):
            case.created_at = _midnight(opened)
            self.dated.discard(case.id)
            changed = True
        if (family := v.get("family")) is not None:
            self.families[family].append(case.id)
        elif r.blank("family") and case.group_id is not None:
            self.ungrouped.add(case.id)
        await self.session.flush()
        await self._finance(r, case)
        if not made:
            self._changed(r, case, "cases", changed)
        if key is not None:
            self._remember(key, case)
        self._made_id = case.id

    def _consultation_of(self, r: _Row) -> Consultation | None:
        """The consultation the case came from; a family's cases share the consultation
        of the one who came, as when cases are opened from it by hand."""
        code = r.values.get("consultation")
        if code is None:
            return None
        consultation = self._resolve("consultations", code, self.consultations)
        if consultation is None:
            r.warn("consultation", "unknown_code")
        return consultation

    def _new_case(
        self,
        r: _Row,
        contact: Contact,
        destination: uuid.UUID,
        submission: uuid.UUID,
        consultation: Consultation | None,
    ) -> VisaCase:
        """A case started from the given consultation, else from the person's last one
        before it; a consultation that was «thinking» becomes «yes», as when a case is
        opened by hand. It starts on «Дата открытия», else on its earliest date."""
        v = r.values
        dates = [v.get(k) for k in ("submission_on", "decided_on", "questionnaire_due_on")]
        started = min((d for d in dates if d is not None), default=None)
        if consultation is None:
            consultation = max(
                (
                    c
                    for c in self.consultations_of[contact.id]
                    if c.held_on <= min(started or self.today, self.today)
                ),
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
            submission_country_id=submission,
            stage=CaseStage.WAITING,
            outcome=None,
            created_by=self.user_id,
        )
        self.next_number += 1
        known = [d for d in (consultation.held_on if consultation else None, started) if d]
        case.created_at = _midnight(min(known)) if known else _midnight(self.today)
        if "opened_on" not in v:
            self.dated.add(case.id)
        if consultation is not None:
            if consultation.decision in (None, Decision.THINKING):
                consultation.decision = Decision.YES
            self.with_case.add(consultation.id)
        self._write(r, case, "cases")
        self.cases[case.id] = case
        self.cases_of[contact.id].append(case)
        return case

    def _outcome(self, r: _Row, case: VisaCase) -> bool:
        """The result, and what goes with it: the entries and the visa's dates only with a
        visa, the decision day and the appeal only with a decision."""
        v = r.values
        changed = False
        if "outcome" in v or r.blank("outcome"):
            outcome = VisaOutcome(v["outcome"]) if "outcome" in v else None
            if case.outcome != outcome:
                case.outcome = outcome
                for name, value in cleared_by_outcome(outcome).items():
                    setattr(case, name, value)
                changed = True
        visa = case.outcome == VisaOutcome.VISA
        decided = case.outcome in (VisaOutcome.VISA, VisaOutcome.REFUSAL)
        details: dict[str, tuple[str, Any]] = {}
        for key, allowed in (
            ("visa_entries", visa),
            ("visa_valid_from", visa),
            ("visa_valid_to", visa),
            ("decided_on", case.outcome is not None),
        ):
            value = v.get(key)
            if value is not None and not allowed:
                r.warn(key, "needs_outcome")
                value = None
            if key == "visa_entries" and value is not None:
                value = VisaEntries(value)
            details[key] = (key, value)
        start = details["visa_valid_from"][1] or case.visa_valid_from
        end = details["visa_valid_to"][1] or case.visa_valid_to
        if start and end and end < start:
            r.warn("visa_valid_to", "date_order")
            details["visa_valid_to"] = ("visa_valid_to", None)
        if (stay := v.get("stay_days")) is not None and stay not in STAY_DAYS:
            r.warn("stay_days", "out_of_range")
            stay = None
        details["stay_days"] = ("stay_days", stay)
        changed |= self._assign(r, case, details)
        appeal = v.get("after_appeal")
        if appeal is None and r.blank("after_appeal"):
            appeal = False
        if appeal and not decided:
            r.warn("after_appeal", "needs_outcome")
            appeal = None
        if appeal is not None and case.after_appeal != appeal:
            case.after_appeal = appeal
            changed = True
        return changed

    def _stage(self, r: _Row, case: VisaCase) -> bool:
        """The stage from the file, else from the data: a result ends the case, a
        submission in the past puts it under review, one ahead — documents are collected.
        Worked out, the stage only moves on."""
        if "stage" in r.values:
            stage = CaseStage(r.values["stage"])
        else:
            if case.outcome is not None:
                stage = CaseStage.RESULT
            elif case.submission_on is not None:
                stage = (
                    CaseStage.REVIEW if case.submission_on <= self.today else CaseStage.DOCUMENTS
                )
            else:
                stage = CaseStage.WAITING
            if STAGE_ORDER.index(stage) <= STAGE_ORDER.index(case.stage):
                return False
        if case.stage == stage:
            return False
        case.stage = stage
        return True

    def _remember(self, key: str, case: VisaCase) -> None:
        record = self.memory.get(key)
        entities = {"case": str(case.id)}
        if record is None:
            record = ImportRow(
                id=uuid.uuid4(),
                tenant_id=self.tenant_id,
                kind=ImportKind.WORKBOOK,
                key=key,
                run_id=self.run_id,
                entities=entities,
            )
            self.session.add(record)
            self.memory[key] = record
        elif record.entities != entities:
            record.entities = entities
            record.run_id = self.run_id

    async def _finance(self, r: _Row, case: VisaCase) -> bool:
        """The case's money: the price, the talon to the registrar (in its currency, at
        the National Bank rate on its day unless a rate is given) and the partner's
        payout."""
        v = r.values
        keys = [c.key for c in r.sheet.spec.columns if c.module == "finances"]
        if not any(k in v or r.blank(k) for k in keys):
            return False
        finance = self.finances.get(case.id)
        made = finance is None
        if finance is None:
            finance = CaseFinance(case_id=case.id, tenant_id=self.tenant_id)
        prepayment = v.get("prepayment")
        if prepayment is not None and prepayment <= 0:
            prepayment = None
        changed = self._assign(
            r,
            finance,
            {"price": ("price", v.get("price")), "prepayment": ("prepayment", prepayment)},
        )
        credit = v.get("consultation_credit")
        if credit is None and r.blank("consultation_credit"):
            credit = Decimal(0)
        if credit is not None and finance.consultation_credit != credit:
            finance.consultation_credit = credit
            changed = True
        changed |= await self._talon(r, case, finance)
        changed |= self._partner(r, finance)
        if made:
            if not changed:
                return False
            self.session.add(finance)
            self.finances[case.id] = finance
            r.created["finances"] += 1
        elif changed:
            r.updated["finances"] += 1
        return changed

    async def _talon(self, r: _Row, case: VisaCase, finance: CaseFinance) -> bool:
        v = r.values
        amount = v.get("registrar_fee")
        if amount is None:
            if not r.blank("registrar_fee") or finance.registrar_fee_amount is None:
                return False
            for name in ("amount", "currency", "rate", "base", "on"):
                setattr(finance, f"registrar_fee_{name}", None)
            finance.registrar_fee_settled = False
            return True
        if case.registrar_id is None:
            # A talon is paid to the registrar: the case has none.
            r.warn("registrar_fee", "talon_without_registrar")
            return False
        currency = v.get("registrar_fee_currency") or self.base
        day = v.get("registrar_fee_on") or finance.registrar_fee_on or case.submission_on
        day = day or self.today
        try:
            rate = await self._rate(r, currency, v.get("registrar_fee_rate"))
        except SkipRow:
            r.warn("registrar_fee", "rate_unavailable")
            return False
        changes = {
            "registrar_fee_amount": amount,
            "registrar_fee_currency": currency,
            "registrar_fee_rate": rate,
            "registrar_fee_base": to_base(amount, rate),
            "registrar_fee_on": day,
        }
        if "registrar_fee_settled" in v:
            changes["registrar_fee_settled"] = v["registrar_fee_settled"]
        changed = False
        for name, value in changes.items():
            if getattr(finance, name) != value:
                setattr(finance, name, value)
                changed = True
        return changed

    def _partner(self, r: _Row, finance: CaseFinance) -> bool:
        """The partner's terms of the case; a percent or a sum without terms says them."""
        v = r.values
        terms = PartnerTermsType(v["partner_terms"]) if "partner_terms" in v else None
        percent, fixed = v.get("partner_percent"), v.get("partner_fixed")
        if terms is None:
            terms = (
                PartnerTermsType.PERCENT
                if percent is not None
                else PartnerTermsType.FIXED
                if fixed is not None
                else None
            )
        if terms is None and not r.blank("partner_terms"):
            if not any(k in v for k in ("partner_paid_on",)):
                return False
            terms = finance.partner_terms
        if percent is not None and terms != PartnerTermsType.PERCENT:
            r.warn("partner_percent", "partner_terms")
            percent = None
        if fixed is not None and terms != PartnerTermsType.FIXED:
            r.warn("partner_fixed", "partner_terms")
            fixed = None
        paid_on = v.get("partner_paid_on")
        if paid_on is not None and terms not in (PartnerTermsType.PERCENT, PartnerTermsType.FIXED):
            r.warn("partner_paid_on", "partner_terms")
            paid_on = None
        wanted = {
            "partner_terms": terms,
            "partner_percent": percent if terms == PartnerTermsType.PERCENT else None,
            "partner_fixed": fixed if terms == PartnerTermsType.FIXED else None,
            "partner_paid_on": paid_on,
        }
        if not r.snapshot:
            # Values the file leaves out stay as they are, when the terms allow them.
            for name in ("partner_percent", "partner_fixed", "partner_paid_on"):
                if wanted[name] is None and terms == finance.partner_terms:
                    wanted[name] = getattr(finance, name)
        changed = False
        for name, value in wanted.items():
            if getattr(finance, name) != value:
                setattr(finance, name, value)
                changed = True
        return changed

    async def _families(self, title: str) -> None:
        """Cases with one «Семья» code apply together: one submission group. A group the
        cases already have is kept; one case alone is not grouped."""
        try:
            async with self.session.begin_nested():
                for case_id in self.ungrouped:
                    self.cases[case_id].group_id = None
                    if case_id not in self.made:
                        self.report.updated["cases"] += 1
                for members in self.families.values():
                    cases = [self.cases[i] for i in dict.fromkeys(members)]
                    groups = [c.group_id for c in cases if c.group_id is not None]
                    if len(cases) < 2 and not groups:
                        continue
                    if groups:
                        group_id = groups[0]
                    else:
                        group = SubmissionGroup(id=uuid.uuid4(), tenant_id=self.tenant_id)
                        self.session.add(group)
                        await self.session.flush()
                        self.report.created["groups"] += 1
                        group_id = group.id
                    for case in cases:
                        if case.group_id != group_id:
                            case.group_id = group_id
                            if case.id not in self.made:
                                self.report.updated["cases"] += 1
                await self.session.flush()
        except SQLAlchemyError:
            self.report.skip(Issue("row_error", title))
            await self.load()
        self.families.clear()
        self.ungrouped.clear()

    # --- Payments and expenses ----------------------------------------------------------

    async def _payments(self, r: _Row) -> None:
        v = r.values
        case = self._case_of(r)
        payment: CasePayment | None = self._own(r, self.payments)
        if payment is not None and payment.case_id != case.id:
            raise SkipRow("code_mismatch", "case")
        amount: Decimal = v["amount"]
        if amount <= 0:
            raise SkipRow("money", "amount")
        currency = v.get("currency") or self.base
        rate = await self._rate(r, currency, v.get("rate"))
        if payment is None:
            payment = next(
                (
                    p
                    for p in self.payments_of[case.id]
                    if p.case_id == case.id
                    and (p.paid_on, p.amount, p.currency) == (v["paid_on"], amount, currency)
                ),
                None,
            )
        made = payment is None
        if payment is None:
            payment = CasePayment(
                id=uuid.uuid4(),
                tenant_id=self.tenant_id,
                case_id=case.id,
                amount=amount,
                currency=currency,
                rate=rate,
                amount_base=to_base(amount, rate),
                paid_on=v["paid_on"],
                official=False,
                created_by=self.user_id,
            )
            self._write(r, payment, "payments")
            self.payments[payment.id] = payment
            self.payments_of[case.id].append(payment)
        r.wrote = True
        changed = self._assign(
            r,
            payment,
            {
                "paid_on": ("paid_on", v["paid_on"]),
                "amount": ("amount", amount),
                "currency": ("currency", currency),
                "rate": ("rate", rate),
                "amount_base": ("amount", to_base(amount, rate)),
                "note": ("note", v.get("note")),
            },
        )
        official = v.get("official")
        if official is None and r.blank("official"):
            official = False
        if official is not None and payment.official != official:
            payment.official = official
            changed = True
        if not made:
            self._changed(r, payment, "payments", changed)
        self._made_id = payment.id

    async def _expenses(self, r: _Row) -> None:
        v = r.values
        case = self._case_of(r)
        expense: CaseExpense | None = self._own(r, self.expenses)
        if expense is not None and expense.case_id != case.id:
            raise SkipRow("code_mismatch", "case")
        amount: Decimal = v["amount"]
        if amount <= 0:
            raise SkipRow("money", "amount")
        currency = v.get("currency") or self.base
        rate = await self._rate(r, currency, v.get("rate"))
        description = v["description"]
        if expense is None:
            expense = next(
                (
                    e
                    for e in self.expenses_of[case.id]
                    if e.case_id == case.id
                    and (e.spent_on, e.amount) == (v["spent_on"], amount)
                    and normalize(e.description) == normalize(description)
                ),
                None,
            )
        made = expense is None
        if expense is None:
            expense = CaseExpense(
                id=uuid.uuid4(),
                tenant_id=self.tenant_id,
                case_id=case.id,
                amount=amount,
                currency=currency,
                rate=rate,
                amount_base=to_base(amount, rate),
                spent_on=v["spent_on"],
                description=description,
                created_by=self.user_id,
            )
            self._write(r, expense, "expenses")
            self.expenses[expense.id] = expense
            self.expenses_of[case.id].append(expense)
        r.wrote = True
        changed = self._assign(
            r,
            expense,
            {
                "spent_on": ("spent_on", v["spent_on"]),
                "description": ("description", description),
                "amount": ("amount", amount),
                "currency": ("currency", currency),
                "rate": ("rate", rate),
                "amount_base": ("amount", to_base(amount, rate)),
            },
        )
        if not made:
            self._changed(r, expense, "expenses", changed)
        self._made_id = expense.id

    # --- Queue and windowed applications ------------------------------------------------

    async def _queue(self, r: _Row) -> None:
        v = r.values
        case = self._case_of(r)
        entry: WaitingListEntry | None = self._own(r, self.entries)
        if entry is not None and entry.case_id != case.id:
            raise SkipRow("code_mismatch", "case")
        queued_on: date = v["queued_on"]
        mine = self.entries_of[case.id]
        if entry is None:
            entry = next((e for e in mine if e.queued_on == queued_on), None)
        dates: dict[str, Any] = {}
        for key in ("letter_on", "removed_on"):
            day = v.get(key)
            if day is None and not r.blank(key) and entry is not None:
                day = getattr(entry, key)
            if day is not None and day < queued_on:
                r.warn(key, "date_order")
                day = None
            dates[key] = day
        reason = v.get("removed_reason")
        if reason is None and not r.blank("removed_reason") and entry is not None:
            reason = entry.removed_reason
        if reason is not None and dates["removed_on"] is None:
            r.warn("removed_reason", "needs_removal")
            reason = None
        if dates["removed_on"] is None and any(
            e.removed_on is None and e is not entry for e in mine
        ):
            raise SkipRow("open_entry_exists")
        made = entry is None
        if entry is None:
            entry = WaitingListEntry(
                id=uuid.uuid4(),
                tenant_id=self.tenant_id,
                case_id=case.id,
                queued_on=queued_on,
                created_by=self.user_id,
            )
            self._write(r, entry, "queue_entries")
            self.entries[entry.id] = entry
            mine.append(entry)
        r.wrote = True
        changed = False
        for name, value in (("queued_on", queued_on), *dates.items(), ("removed_reason", reason)):
            if getattr(entry, name) != value:
                setattr(entry, name, value)
                changed = True
        self._starts(case, queued_on)
        if not made:
            self._changed(r, entry, "queue_entries", changed)
        self._made_id = entry.id

    async def _attempts(self, r: _Row) -> None:
        v = r.values
        case = self._case_of(r)
        attempt: ApplicationAttempt | None = self._own(r, self.attempts)
        if attempt is not None and attempt.case_id != case.id:
            raise SkipRow("code_mismatch", "case")
        sent_on: date = v["sent_on"]
        mine = self.attempts_of[case.id]
        number = v.get("number")
        if attempt is None and number is not None:
            attempt = next((a for a in mine if a.number == number), None)
        if attempt is None:
            attempt = next((a for a in mine if a.sent_on == sent_on), None)
        if number is not None and any(a.number == number and a is not attempt for a in mine):
            raise SkipRow("number_taken", "number")
        due = v.get("deadline_on")
        if due is not None and due < sent_on:
            r.warn("deadline_on", "date_order")
            due = None
        if due is None:
            window = self.windows.get((case.destination_id, case.submission_country_id))
            if window is None:
                raise SkipRow("no_window", "deadline_on")
            due = deadline(sent_on, window)
        outcome = AttemptOutcome(v["outcome"]) if "outcome" in v else None
        if outcome is None and not r.blank("outcome") and attempt is not None:
            outcome = attempt.outcome
        closed = v.get("closed_on")
        if closed is None and not r.blank("closed_on") and attempt is not None:
            closed = attempt.closed_on
        if closed is not None and outcome is None:
            r.warn("closed_on", "needs_outcome")
            closed = None
        if outcome is None and any(a.outcome is None and a is not attempt for a in mine):
            raise SkipRow("open_attempt_exists")
        made = attempt is None
        if attempt is None:
            taken = [a.number for a in mine]
            attempt = ApplicationAttempt(
                id=uuid.uuid4(),
                tenant_id=self.tenant_id,
                case_id=case.id,
                number=number or max(taken, default=0) + 1,
                sent_on=sent_on,
                deadline_on=due,
                created_by=self.user_id,
            )
            self._write(r, attempt, "attempts")
            self.attempts[attempt.id] = attempt
            mine.append(attempt)
        r.wrote = True
        changed = False
        for name, value in (
            ("number", number or attempt.number),
            ("sent_on", sent_on),
            ("deadline_on", due),
            ("outcome", outcome),
            ("closed_on", closed),
        ):
            if getattr(attempt, name) != value:
                setattr(attempt, name, value)
                changed = True
        self._starts(case, sent_on)
        if not made:
            self._changed(r, attempt, "attempts", changed)
        self._made_id = attempt.id

    # --- Accounts and bookings ----------------------------------------------------------

    async def _accounts(self, r: _Row) -> None:
        v = r.values
        system = self._find(ValueType.SYSTEM, v["system"])
        if system is None:
            self.report.seen_unknown(ValueType.SYSTEM, normalize(v["system"]), v["system"])
            raise SkipRow("unknown_system", "system")
        contact = self._client_of(r, required=False)
        case = self._case_of(r) if "case" in v else None
        if case is not None:
            if contact is None:
                contact = self.contacts[case.contact_id]
            elif case.contact_id != contact.id:
                raise SkipRow("code_mismatch", "case")
        entry: VaultEntry | None = self._own(r, self.vault)
        contact_id = contact.id if contact else None
        if entry is not None and entry.contact_id != contact_id:
            raise SkipRow("code_mismatch", "client")
        login: str = v["login"]
        if entry is None:
            found = self.accounts.get((contact_id, system, login.casefold()))
            entry = self.vault.get(found) if found else None  # type: ignore[arg-type]
        made = entry is None
        if entry is None:
            entry = VaultEntry(
                id=uuid.uuid4(),
                tenant_id=self.tenant_id,
                system_id=system,
                contact_id=contact_id,
                case_id=case.id if case else None,
                login=login,
                created_by=self.user_id,
            )
            self._write(r, entry, "vault_entries")
        r.wrote = True
        owner = EmailOwner(v["email_owner"]) if "email_owner" in v else None
        changed = self._assign(
            r,
            entry,
            {
                "login": ("login", login),
                "system_id": ("system", system),
                "case_id": ("case", case.id if case else None),
                "email_owner": ("email_owner", owner),
                "extra_id": ("extra_id", v.get("extra_id")),
                "note": ("note", v.get("note")),
            },
        )
        manual = v.get("created_manually")
        if manual is None and r.blank("created_manually"):
            manual = False
        if manual is not None and entry.created_manually != manual:
            entry.created_manually = manual
            changed = True
        await self.session.flush()
        if (password := v.get("password")) and await self._set_password(entry, password):
            r.created["passwords"] += 1
            changed = True
        self._index_account(entry)
        if not made:
            self._changed(r, entry, "vault_entries", changed)
        self._made_id = entry.id

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

    async def _bookings(self, r: _Row) -> None:
        v = r.values
        case = self._case_of(r)
        booking: Booking | None = self._own(r, self.bookings)
        if booking is not None and booking.case_id != case.id:
            raise SkipRow("code_mismatch", "case")
        mine = self.bookings_of[case.id]
        place: str = v["place"]
        if booking is None and (reference := v.get("reference")):
            booking = next((b for b in mine if b.reference == reference), None)
        if booking is None:
            booking = next(
                (
                    b
                    for b in mine
                    if normalize(b.place) == normalize(place) and b.starts_on == v.get("starts_on")
                ),
                None,
            )
        kind = BookingKind(v["kind"]) if "kind" in v else (booking.kind if booking else None)
        kind = kind or BookingKind.HOTEL
        dates: dict[str, tuple[str, Any]] = {
            k: (k, v.get(k)) for k in ("starts_on", "ends_on", "free_cancel_until", "valid_until")
        }
        start = dates["starts_on"][1] or (booking.starts_on if booking else None)
        end = dates["ends_on"][1] or (booking.ends_on if booking else None)
        if start and end and end < start:
            r.warn("ends_on", "date_order")
            dates["ends_on"] = ("ends_on", None)
        for key, allowed in (
            ("free_cancel_until", BookingKind.HOTEL),
            ("valid_until", BookingKind.FLIGHT),
        ):
            if dates[key][1] is not None and kind != allowed:
                r.warn(key, "booking_kind")
                dates[key] = (key, None)
        account = None
        if (login := v.get("account")) is not None:
            account = self.logins.get(login.casefold())
            if account is None:
                r.warn("account", "unknown_value")
        made = booking is None
        if booking is None:
            booking = Booking(
                id=uuid.uuid4(),
                tenant_id=self.tenant_id,
                case_id=case.id,
                kind=kind,
                status=BookingStatus.ACTIVE,
                place=place,
                created_by=self.user_id,
            )
            self._write(r, booking, "bookings")
            self.bookings[booking.id] = booking
            mine.append(booking)
        r.wrote = True
        status = BookingStatus(v["status"]) if "status" in v else None
        changed = self._assign(
            r,
            booking,
            {
                "kind": ("kind", kind),
                "status": ("status", status),
                "place": ("place", place),
                **dates,
                "reference": ("reference", v.get("reference")),
                "vault_entry_id": ("account", account),
                "note": ("note", v.get("note")),
            },
        )
        if kind != BookingKind.HOTEL and booking.free_cancel_until is not None:
            booking.free_cancel_until = None
        if kind != BookingKind.FLIGHT and booking.valid_until is not None:
            booking.valid_until = None
        if not made:
            self._changed(r, booking, "bookings", changed)
        self._made_id = booking.id
