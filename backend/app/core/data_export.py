"""Files in the VisaCRM data format the CRM gives (backlog 1.21, docs/data-format.md →
«Выгрузка»): the template and the export of a workspace. One writer for both: every sheet
with its headers, the «Справочник» with the values the workspace's lists and the format's
fixed words allow, drop-downs from it, and «О файле». The template is an export with no
rows; the specialist gives it to the AI with the prompt (docs/import-prompt.md).

The export writes the workspace's records as the import reads them back (app/core/
data_records.py): codes are the CRM's ids, values the format's words in the interface's
language, dates and sums real cells. «О файле» says when the file was exported, so its rows
are snapshots of their records and a record changed in the CRM since is not loaded over
(docs/data-format.md → «Повторная загрузка»). Sheets and columns of a module the workspace
has not are left out: loaded back once the module is on, their empty cells would clear it.

Without passwords the «Пароль» column is empty: an empty cell never clears a password.
With them, the vault is decrypted here, for the export the specialist confirmed.
"""

import asyncio
import io
import uuid
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from openpyxl import Workbook
from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.data_format import (
    ABOUT_EXPORTED,
    ABOUT_SHEET,
    REFERENCE_SHEET,
    SHEETS,
    VERSION,
    Choice,
    Column,
    ColumnKind,
    Sheet,
    ValueType,
)
from app.core.data_records import Localized, Record, Records
from app.core.money import CURRENCIES
from app.core.vault import decrypt_for_tenant, get_tenant_key
from app.models import (
    Country,
    CountryRule,
    LeadChannel,
    Partner,
    Registrar,
    Tenant,
    VaultSystem,
    VisaCenter,
    VisaPurpose,
)

# Rows a drop-down covers below the header, past the rows an export fills.
LIST_ROWS = 5000
HEADER_FONT = Font(bold=True)
KEY_FONT = Font(bold=True, color="808080")
REFERENCE_FILL = PatternFill("solid", fgColor="E7E6E6")
DATE_FORMAT = "DD.MM.YYYY"
TIME_FORMAT = "DD.MM.YYYY HH:MM:SS"
YES_NO = (Choice("yes", "да", "yes"), Choice("no", "нет", "no"))


@dataclass
class Lists:
    """The workspace's values for the «Справочник»."""

    countries: list[str] = field(default_factory=list)
    cities: list[str] = field(default_factory=list)
    channels: list[str] = field(default_factory=list)
    partners: list[str] = field(default_factory=list)
    purposes: list[str] = field(default_factory=list)
    registrars: list[str] = field(default_factory=list)
    systems: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Export:
    content: bytes
    # Rows written, by sheet key: the audit log keeps the counts only.
    rows: dict[str, int]
    # Passwords written to the file: none without passwords.
    passwords: int


_LIST_OF_TYPE = {
    ValueType.COUNTRY: "countries",
    ValueType.CITY: "cities",
    ValueType.CHANNEL: "channels",
    ValueType.PARTNER: "partners",
    ValueType.PURPOSE: "purposes",
    ValueType.REGISTRAR: "registrars",
    ValueType.SYSTEM: "systems",
}
_LIST_TITLES = {
    "countries": ("Страны", "Countries"),
    "cities": ("Города подачи", "Application cities"),
    "channels": ("Каналы прихода", "Lead channels"),
    "partners": ("Партнёры", "Partners"),
    "purposes": ("Цели", "Purposes"),
    "registrars": ("Регистраторы", "Registrars"),
    "systems": ("Системы сейфа", "Vault systems"),
    "currencies": ("Валюты", "Currencies"),
    "yes_no": ("Да / нет", "Yes / no"),
}


def _word(language: str, ru: str, en: str) -> str:
    return ru if language == "ru" else en


def _values(column: Column, lists: Lists, language: str) -> tuple[str, list[str]] | None:
    """The «Справочник» column a column's drop-down takes its values from."""
    if column.kind != ColumnKind.DATA:
        return None
    if column.type == ValueType.CHOICE:
        return f"{column.key}", [_word(language, c.ru, c.en) for c in column.choices]
    if column.type == ValueType.BOOL:
        return "yes_no", [_word(language, c.ru, c.en) for c in YES_NO]
    if column.type == ValueType.CURRENCY:
        return "currencies", list(CURRENCIES)
    if (name := _LIST_OF_TYPE.get(column.type)) is not None:
        return name, getattr(lists, name)
    return None


def cell_value(column: Column, value: Any, language: str) -> Any:
    """A record's value as its cell: the format's words in the language, real dates and
    numbers, text as it is. None: an empty cell."""
    if value is None or value == []:
        return None
    if isinstance(value, bool):
        yes_no = YES_NO[0] if value else YES_NO[1]
        return _word(language, yes_no.ru, yes_no.en)
    if isinstance(value, Localized):
        return getattr(value, language)
    if isinstance(value, list):
        return ", ".join(str(cell_value(column, item, language)) for item in value)
    if column.choices:
        choice = next((c for c in column.choices if c.value == value), None)
        # A value the format has no word for is written as it is: the import reports its
        # cell instead of the whole export failing.
        return _word(language, choice.ru, choice.en) if choice else str(value)
    if column.type == ValueType.DATE and isinstance(value, str):
        # The passport's dates are kept as ISO text on the card's questionnaire.
        return date.fromisoformat(value)
    return value


def workbook(
    lists: Lists,
    *,
    language: str,
    workspace: str,
    base_currency: str,
    sheets: Iterable[tuple[Sheet, tuple[Column, ...]]] | None = None,
    rows: dict[str, list[dict[str, Any]]] | None = None,
    exported_at: datetime | None = None,
    with_passwords: bool | None = None,
) -> bytes:
    """A workbook of the format in the interface's language: the template without rows,
    an export with them. Sheets: each with the columns to write (all by default)."""
    book = Workbook()
    book.remove(book.active)
    chosen = list(sheets) if sheets is not None else [(s, s.columns) for s in SHEETS]
    data_sheets = [
        (spec, columns, book.create_sheet(_word(language, spec.ru, spec.en)))
        for spec, columns in chosen
    ]
    lists_sheet = book.create_sheet(_word(language, *REFERENCE_SHEET))
    ranges: dict[str, str] = {}

    def list_range(name: str, title: str, values: list[str]) -> str | None:
        if not values:
            return None
        if name not in ranges:
            col = len(ranges) + 1
            letter = get_column_letter(col)
            head = lists_sheet.cell(1, col, title)
            head.font, head.fill = HEADER_FONT, REFERENCE_FILL
            for row, value in enumerate(values, start=2):
                _set(lists_sheet.cell(row, col), value)
            lists_sheet.column_dimensions[letter].width = max(16, len(title) + 2)
            quoted = lists_sheet.title.replace("'", "''")
            ranges[name] = f"'{quoted}'!${letter}$2:${letter}${len(values) + 1}"
        return ranges[name]

    for spec, columns, sheet in data_sheets:
        records = (rows or {}).get(spec.key, [])
        last_row = len(records) + 1 + LIST_ROWS
        for col, column in enumerate(columns, start=1):
            letter = get_column_letter(col)
            cell = sheet.cell(1, col, _word(language, column.ru, column.en))
            cell.font = KEY_FONT if column.kind == ColumnKind.KEY else HEADER_FONT
            cell.alignment = Alignment(wrap_text=True, vertical="top")
            if column.kind == ColumnKind.REF:
                cell.fill = REFERENCE_FILL
            sheet.column_dimensions[letter].width = max(10, min(28, len(cell.value) + 2))
            found = _values(column, lists, language)
            if found is None:
                continue
            name, values = found
            title = _LIST_TITLES.get(name, (column.ru, column.en))
            target = list_range(name, _word(language, *title), values)
            if target is None:
                continue
            check = DataValidation(type="list", formula1=f"={target}", allow_blank=True)
            check.showErrorMessage = column.type in (ValueType.CHOICE, ValueType.BOOL)
            sheet.add_data_validation(check)
            check.add(f"{letter}2:{letter}{last_row}")
        for number, record in enumerate(records, start=2):
            for col, column in enumerate(columns, start=1):
                value = cell_value(column, record.get(column.key), language)
                if value is not None:
                    _set(sheet.cell(number, col), value)
        sheet.row_dimensions[1].height = 32
        sheet.freeze_panes = "B2"
    lists_sheet.freeze_panes = "A2"

    about = book.create_sheet(_word(language, *ABOUT_SHEET))
    facts: list[tuple[str, Any]] = [
        (
            _word(language, "Формат", "Format"),
            _word(language, f"VisaCRM, версия {VERSION}", f"VisaCRM, version {VERSION}"),
        ),
        (_word(language, "Кабинет", "Workspace"), workspace),
        (_word(language, "Базовая валюта", "Base currency"), base_currency),
    ]
    if exported_at is not None:
        facts.append((_word(language, *ABOUT_EXPORTED), exported_at))
        facts.append(
            (
                _word(language, "Пароли", "Passwords"),
                _word(language, "с паролями", "with passwords")
                if with_passwords
                else _word(language, "без паролей", "without passwords"),
            )
        )
    for number, (key, value) in enumerate(facts, start=1):
        about.cell(number, 1, key).font = HEADER_FONT
        _set(about.cell(number, 2), value)
    about.column_dimensions["A"].width = 18
    about.column_dimensions["B"].width = 48

    out = io.BytesIO()
    book.save(out)
    return out.getvalue()


def _set(cell: Any, value: Any) -> None:
    if isinstance(value, str):
        # Characters XML cannot hold (pasted control codes) would break the whole file.
        value = ILLEGAL_CHARACTERS_RE.sub("", value)
    cell.value = value
    if isinstance(value, str):
        # Text stays text: «=…» typed in the CRM is never a formula of the file.
        cell.data_type = "s"
    elif isinstance(value, datetime):
        cell.number_format = TIME_FORMAT
    elif isinstance(value, date):
        cell.number_format = DATE_FORMAT


def template(lists: Lists, *, language: str, workspace: str, base_currency: str) -> bytes:
    """The empty workbook of the format, in the interface's language."""
    return workbook(lists, language=language, workspace=workspace, base_currency=base_currency)


async def _names(session: AsyncSession, model: Any) -> list[str]:
    rows = await session.scalars(select(model.name).where(model.is_active).order_by(model.name))
    return list(rows)


async def workspace_lists(session: AsyncSession, language: str) -> Lists:
    """The workspace's values for the «Справочник»."""
    country = Country.name_ru if language == "ru" else Country.name_en
    # The English name of a city is optional: the Russian one stands in for it.
    city = (
        VisaCenter.city_ru
        if language == "ru"
        else func.coalesce(VisaCenter.city_en, VisaCenter.city_ru)
    )
    cities = await session.scalars(
        select(city).join(CountryRule, VisaCenter.rule_id == CountryRule.id).distinct()
    )
    return Lists(
        countries=list(await session.scalars(select(country).order_by(country))),
        cities=sorted(set(cities)),
        channels=await _names(session, LeadChannel),
        partners=await _names(session, Partner),
        purposes=await _names(session, VisaPurpose),
        registrars=await _names(session, Registrar),
        systems=await _names(session, VaultSystem),
    )


def sheets_of(modules: set[str]) -> list[tuple[Sheet, tuple[Column, ...]]]:
    """The sheets and columns of the modules the workspace has."""
    return [
        (sheet, tuple(c for c in sheet.columns if c.module is None or c.module in modules))
        for sheet in SHEETS
        if sheet.module in modules
    ]


async def export(
    session: AsyncSession,
    *,
    tenant: Tenant,
    modules: set[str],
    language: str,
    with_passwords: bool,
) -> Export:
    """Every record of the workspace in one file of the format. The session must be the
    owner's, in the workspace's scope: it sees every record. With passwords, the vault's
    are decrypted into the file; the caller has had them confirmed and logs it."""
    zone = ZoneInfo(tenant.timezone)
    # The database's clock, the one the records' updated_at are stamped by; to the
    # millisecond a file keeps, rounded down: a record changed in that millisecond counts
    # as changed after the export, and its old row is not loaded over it.
    now: datetime = await session.scalar(select(func.now()))  # type: ignore[assignment]
    local = now.astimezone(zone).replace(tzinfo=None)
    exported_at = local.replace(microsecond=local.microsecond // 1000 * 1000)
    records = Records(session, base=tenant.base_currency, zone=zone, modules=modules)
    await records.load()
    rows = _rows(records)
    passwords = 0
    if with_passwords:
        key = await get_tenant_key(session, tenant.id)
        for row in rows.get("accounts", []):
            entry = records.vault[row["entry_id"]]
            if entry.password_encrypted is not None:
                row["password"] = decrypt_for_tenant(
                    key, tenant.id, entry.id, entry.password_encrypted
                )
                passwords += 1
    lists = await workspace_lists(session, language)
    # The writing takes a while for a big workspace: not in the event loop.
    content = await asyncio.to_thread(
        workbook,
        lists,
        language=language,
        workspace=tenant.name,
        base_currency=tenant.base_currency,
        sheets=sheets_of(modules),
        rows=rows,
        exported_at=exported_at,
        with_passwords=with_passwords,
    )
    return Export(content, {key: len(found) for key, found in rows.items()}, passwords)


def _rows(records: Records) -> dict[str, list[dict[str, Any]]]:
    """The records by sheet: the clients in the order of their names, each with all their
    records, then the specialist's own accounts."""
    out: dict[str, list[dict[str, Any]]] = {}
    order = sorted(records.contacts.values(), key=lambda c: (c.full_name.casefold(), c.id))
    for contact in order:
        for record in records.client(contact.id) or ():
            _flatten(record, out)
    for record in records.own():
        _flatten(record, out)
    return out


def _flatten(record: Record, out: dict[str, list[dict[str, Any]]]) -> None:
    row = record.values | record.links
    if record.sheet == "accounts":
        # The vault entry, to decrypt its password for an export with passwords; the
        # card's «есть» is never written.
        row["entry_id"] = uuid.UUID(record.links["code"])
        row["password"] = None
    out.setdefault(record.sheet, []).append(row)
    for child in record.children:
        _flatten(child, out)
