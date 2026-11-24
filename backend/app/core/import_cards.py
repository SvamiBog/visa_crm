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

from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING, Any, Literal

from app.core.data_format import SHEETS, Column, Sheet, ValueType
from app.core.data_records import Change, Localized, Record, Records, walk

if TYPE_CHECKING:
    from app.core.data_import import WorkbookImport

Status = Literal["new", "updated", "same"]


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

    def change(record_id: Any) -> Change:
        if record_id in run.made:
            return "new"
        return "updated" if record_id in run.changed else None

    records = Records(run.session, base=run.base, zone=run.zone, modules=run.modules, change=change)
    await records.load()
    made = [_card(records, cid, issues) for cid, issues in run.touched.items() if cid is not None]
    if None in run.touched:
        made.append(_own(records, run.touched[None]))
    return [card for card in made if card is not None]


def _card(records: Records, contact_id: Any, issues: list[dict[str, Any]]) -> Card | None:
    found = records.client(contact_id)
    if found is None:
        # Made by a row the database refused: rolled back, it is no card.
        return None
    client = found[0]
    contact = records.contacts[contact_id]
    changed = any(r.change for r in walk(found))
    status: Status = "new" if client.change == "new" else "updated" if changed else "same"
    return Card(status, contact.full_name, contact.phone, found, tuple(issues))


def _own(records: Records, issues: list[dict[str, Any]]) -> Card:
    """The specialist's own accounts: the card of no client."""
    found = tuple(records.own())
    changes = [r.change for r in found]
    status: Status = (
        "new"
        if changes and all(c == "new" for c in changes)
        else "updated"
        if any(changes)
        else "same"
    )
    return Card(status, None, None, found, tuple(issues))


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
