"""Cells of a file in the VisaCRM data format (backlog 1.20, docs/data-format.md → «Общие
правила»): read strictly, without guessing. A date is a date cell or ДД.ММ.ГГГГ, a sum is a
number, a list value is one of the format's words. Turning the specialist's own spellings
into these is the AI's job (docs/import-prompt.md). A cell that does not fit raises
CellError with a reason code; the import reports it by sheet, row and column, never with
the value.
"""

import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from app.core.data_format import Choice
from app.core.finance import money
from app.core.money import CURRENCIES
from app.core.phone import normalize_phone

Cell = str | int | float | Decimal | bool | date | datetime | None

_NON_WORD = re.compile(r"[^\w]+")
_DMY = re.compile(r"^(\d{1,2})\.(\d{1,2})\.(\d{4})$")
_YMD = re.compile(r"^(\d{4})-(\d{1,2})-(\d{1,2})$")
_NUMBER = re.compile(r"^\d+(?:\.\d+)?$")
_DOCUMENT_NUMBER = re.compile(r"^[A-Z0-9]{1,20}$")
_PHONE = re.compile(r"^\+[\d\s()\-]+$")
# Telegram usernames: 5–32 letters, digits and underscores, starting with a letter.
_NICK = re.compile(r"^[A-Za-z][A-Za-z0-9_]{4,31}$")
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
EMAIL_LENGTH = 320
_YES = {"да", "yes"}
_NO = {"нет", "no"}
# A one-letter word typed in Cyrillic that looks Latin: «М» for the sex, «Х».
_LOOKALIKES = str.maketrans("мх", "mx")


class CellError(ValueError):
    """A cell that does not fit the format, with the reason for the report (import.reason.*)."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def normalize(value: str) -> str:
    """For comparing headers, sheet names and words: case, «ё» and punctuation do not matter."""
    return " ".join(_NON_WORD.sub(" ", value.casefold().replace("ё", "е")).split())


def cell_text(cell: Cell) -> str | None:
    """The cell as text; None for an empty one. Whole numbers lose ".0": codes and numbers
    at the visa center are often stored as numbers."""
    if cell is None or isinstance(cell, bool):
        return None if cell is None else str(cell)
    if isinstance(cell, datetime):
        cell = cell.date()
    if isinstance(cell, date):
        return cell.strftime("%d.%m.%Y")
    if isinstance(cell, float | Decimal):
        number = Decimal(repr(cell)) if isinstance(cell, float) else cell
        if number == number.to_integral_value():
            return str(int(number))
        return format(number.normalize(), "f")
    text = str(cell).strip()
    return text or None


def parse_text(cell: Cell, max_length: int | None) -> str:
    text = " ".join((cell_text(cell) or "").split()) if not isinstance(cell, str) else cell.strip()
    if max_length is not None and len(text) > max_length:
        raise CellError("too_long")
    return text


def parse_document_number(cell: Cell) -> str:
    """A passport or personal number as in the machine-readable zone."""
    text = (cell_text(cell) or "").replace(" ", "").upper()
    if not _DOCUMENT_NUMBER.match(text):
        raise CellError("document_number")
    return text


def parse_date(cell: Cell) -> date:
    """A date cell, or ДД.ММ.ГГГГ (ГГГГ-ММ-ДД too). A number is not a date here: a file
    in the format has real dates, and a day count could be anything."""
    if isinstance(cell, datetime):
        return cell.date()
    if isinstance(cell, date):
        return cell
    if not isinstance(cell, str):
        raise CellError("date")
    text = cell.strip()
    try:
        if match := _DMY.match(text):
            day, month, year = (int(g) for g in match.groups())
            return date(year, month, day)
        if match := _YMD.match(text):
            year, month, day = (int(g) for g in match.groups())
            return date(year, month, day)
    except ValueError as error:
        raise CellError("date") from error
    raise CellError("date")


def _decimal(cell: Cell, reason: str) -> Decimal:
    if isinstance(cell, bool):
        raise CellError(reason)
    if isinstance(cell, int | Decimal):
        return Decimal(cell)
    if isinstance(cell, float):
        return Decimal(repr(cell))
    text = re.sub(r"[\s  ]", "", str(cell or "")).replace(",", ".")
    if not _NUMBER.match(text):
        raise CellError(reason)
    try:
        return Decimal(text)
    except InvalidOperation as error:
        raise CellError(reason) from error


def parse_int(cell: Cell) -> int:
    number = _decimal(cell, "number")
    if number != number.to_integral_value() or number < 0:
        raise CellError("number")
    return int(number)


def parse_money(cell: Cell) -> Decimal:
    """A sum without its currency, 0 or more, rounded to kopecks (finance.money())."""
    number = _decimal(cell, "money")
    if number < 0 or number >= Decimal("1e12"):
        raise CellError("money")
    return money(number)


def parse_rate(cell: Cell) -> Decimal:
    """An exchange rate: how many units of the base currency one unit costs."""
    number = _decimal(cell, "rate")
    if not Decimal(0) < number < Decimal("1e8"):
        raise CellError("rate")
    return number.quantize(Decimal("0.000001")).normalize()


def parse_percent(cell: Cell) -> Decimal:
    number = _decimal(cell, "percent")
    if not Decimal(0) < number <= 100:
        raise CellError("percent")
    return money(number)


def parse_currency(cell: Cell) -> str:
    """A currency the CRM keeps accounts in (app/core/money.py)."""
    code = (cell_text(cell) or "").upper()
    if code not in CURRENCIES:
        raise CellError("currency")
    return code


def parse_bool(cell: Cell) -> bool:
    if isinstance(cell, bool):
        return cell
    word = normalize(cell_text(cell) or "")
    if word in _YES:
        return True
    if word in _NO:
        return False
    raise CellError("yes_no")


def parse_phone(cell: Cell) -> str:
    """With the country code: «+375291234567»; spaces, brackets and dashes are allowed."""
    if not isinstance(cell, str) or not _PHONE.match(cell.strip()):
        raise CellError("phone")
    try:
        return normalize_phone(cell)
    except ValueError as error:
        raise CellError("phone") from error


def parse_telegram(cell: Cell) -> str:
    """A nick, kept without «@» as the card keeps it."""
    nick = (cell_text(cell) or "").removeprefix("@")
    if not _NICK.match(nick):
        raise CellError("telegram")
    return nick


def parse_email(cell: Cell) -> str:
    text = (cell_text(cell) or "").strip()
    if len(text) > EMAIL_LENGTH or not _EMAIL.match(text):
        raise CellError("email")
    return text


def parse_choice(cell: Cell, choices: tuple[Choice, ...]) -> str:
    """One of the format's words, in Russian or English."""
    word = _word(cell_text(cell) or "")
    for choice in choices:
        if word in (_word(choice.ru), _word(choice.en)):
            return choice.value
    raise CellError("choice")


def _word(value: str) -> str:
    word = normalize(value)
    return word.translate(_LOOKALIKES) if len(word) == 1 else word


def parse_list(cell: Cell) -> list[str]:
    """Several values in one cell, separated by commas."""
    return [part.strip() for part in (cell_text(cell) or "").split(",") if part.strip()]
