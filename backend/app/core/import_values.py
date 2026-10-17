"""Cells of the specialist's spreadsheets (backlog 1.14), typed by hand over the years:
dates in several formats, sums with a currency, «да» or «+», a visa result in words, a
phone and a Telegram nick in one cell. Pure functions; a cell that does not parse raises
ValueError and becomes a warning in the import report — never a guess.
"""

import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from app.core.finance import money
from app.core.phone import normalize_phone
from app.models.consultation import Decision
from app.models.visa_case import VisaEntries, VisaOutcome

Cell = str | int | float | Decimal | bool | date | datetime | None

_NON_WORD = re.compile(r"[^\w]+")


def cell_text(cell: Cell) -> str | None:
    """The cell as text; None for an empty one. Whole numbers lose ".0" (phones and IDs
    are often stored as numbers), dates read as in Belarus: 12.06.2026."""
    if cell is None:
        return None
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


def normalize(value: str) -> str:
    """For comparing names and headers: case, «ё» and punctuation do not matter."""
    return " ".join(_NON_WORD.sub(" ", value.casefold().replace("ё", "е")).split())


def name_key(value: str) -> str:
    """A person's name for matching rows: «Иванова Анна» and «анна иванова» are one."""
    return " ".join(sorted(normalize(value).split()))


# --- Dates and numbers ----------------------------------------------------------------

_DMY = re.compile(r"^(\d{1,2})[./-](\d{1,2})[./-](\d{4}|\d{2})(?:\D.*)?$")
_YMD = re.compile(r"^(\d{4})-(\d{1,2})-(\d{1,2})(?:\D.*)?$")


def parse_date(cell: Cell, today: date) -> date:
    """A date cell, or text «12.06.2026», «12/06/26», «2026-06-12» (day first). A two-digit
    year is this century unless that is more than 10 years ahead: «85» is 1985."""
    if isinstance(cell, datetime):
        day = cell.date()
    elif isinstance(cell, date):
        day = cell
    elif isinstance(cell, str):
        text = cell.strip()
        if match := _YMD.match(text):
            year, month, dom = (int(g) for g in match.groups())
        elif match := _DMY.match(text):
            dom, month, year = (int(g) for g in match.groups())
            if len(match.group(3)) == 2:
                year += 2000 if 2000 + year <= today.year + 10 else 1900
        else:
            raise ValueError("not a date")
        day = date(year, month, dom)
    else:
        raise ValueError("not a date")
    if not 1900 <= day.year <= 2100:
        raise ValueError("date out of range")
    return day


def parse_int(cell: Cell) -> int:
    """A whole number; text may go on after it: «2 человека»."""
    if isinstance(cell, bool):
        raise ValueError("not a number")
    if isinstance(cell, int):
        return cell
    if isinstance(cell, float | Decimal) and cell == int(cell):
        return int(cell)
    if isinstance(cell, str) and (match := re.match(r"^\s*(\d+)(?!\s*[.,]\d)\b", cell)):
        return int(match.group(1))
    raise ValueError("not a whole number")


# Words and signs of the currencies the specialist is paid in.
_CURRENCY_WORDS = (
    ("BYN", ("byn", "byr", "br", "бр", "р", "руб", "бел")),
    ("EUR", ("eur", "евро", "€")),
    ("USD", ("usd", "дол", "$")),
    ("PLN", ("pln", "zł", "zl", "зл", "злот")),
)
_NUMBER = re.compile(r"\d[\d\s  .,]*")
_TOKEN = re.compile(r"[^\W\d_]+|[€$]")


def _currency(text: str) -> str | None:
    found: set[str] = set()
    for token in _TOKEN.findall(text.casefold()):
        for code, words in _CURRENCY_WORDS:
            if any(token == w or (len(w) > 2 and token.startswith(w)) for w in words):
                found.add(code)
    if len(found) > 1:
        raise ValueError("several currencies")
    return found.pop() if found else None


def _decimal(digits: str) -> Decimal:
    """«1 200,50», «1.200,50», «1,200.50», «350» → Decimal: the last separator followed by
    one or two digits is the decimal point, the others group thousands."""
    digits = re.sub(r"[\s  ]", "", digits).rstrip(".,")
    last = max(digits.rfind(","), digits.rfind("."))
    if last != -1 and len(digits) - last - 1 in (1, 2):
        whole, fraction = digits[:last], digits[last + 1 :]
    else:
        whole, fraction = digits, ""
    whole = whole.replace(",", "").replace(".", "")
    try:
        return Decimal(f"{whole}.{fraction}" if fraction else whole)
    except InvalidOperation as error:
        raise ValueError("not a number") from error


def parse_money(cell: Cell, default_currency: str) -> tuple[Decimal, str]:
    """An amount and its currency: «350», «350 р.», «€120», «1 200,50 BYN». One amount
    per cell — «100 + 50» is not added up."""
    if isinstance(cell, bool):
        raise ValueError("not an amount")
    if isinstance(cell, int | float | Decimal):
        amount = Decimal(repr(cell)) if isinstance(cell, float) else Decimal(cell)
        currency = default_currency
    elif isinstance(cell, str):
        numbers = _NUMBER.findall(cell)
        if len(numbers) != 1 or "-" in cell.split(numbers[0])[0]:
            raise ValueError("not one amount")
        amount = _decimal(numbers[0])
        currency = _currency(cell) or default_currency
    else:
        raise ValueError("not an amount")
    if amount < 0:
        raise ValueError("negative amount")
    return money(amount), currency


# --- Words -------------------------------------------------------------------------------

_TRUE = {"да", "yes", "y", "true", "истина", "1", "оплачено", "оплачена", "оплатил", "оплатила"}
_FALSE = {"нет", "no", "n", "false", "ложь", "0", "не оплачено", "не оплачена"}
_TRUE_SIGNS = {"+", "✓", "✔", "✅", "v"}
_FALSE_SIGNS = {"-", "—", "✗", "❌", "x", "х"}


def parse_bool(cell: Cell) -> bool:
    if isinstance(cell, bool):
        return cell
    raw = (cell_text(cell) or "").casefold()
    if raw in _TRUE_SIGNS or normalize(raw) in _TRUE:
        return True
    if raw in _FALSE_SIGNS or normalize(raw) in _FALSE:
        return False
    raise ValueError("not yes or no")


def parse_decision(cell: Cell) -> Decision:
    """Решение по визе: «да», «+», «в работе» / «нет», «отказался» / «думает», «?»."""
    raw = (cell_text(cell) or "").casefold()
    word = normalize(raw)
    if raw in _TRUE_SIGNS or word in {"да", "yes", "y", "в работе", "работаем", "берем"}:
        return Decision.YES
    if word.startswith(("соглас", "взял")):
        return Decision.YES
    if raw in _FALSE_SIGNS or word in {"нет", "no", "n"} or word.startswith(("отказ", "не буд")):
        return Decision.NO
    if raw == "?" or word.startswith(("дума", "подума", "think", "может", "позже")):
        return Decision.THINKING
    raise ValueError("unknown decision")


@dataclass(frozen=True)
class Outcome:
    outcome: VisaOutcome
    entries: VisaEntries | None = None
    after_appeal: bool = False


_NOT_SUBMITTED = (
    "не подал",
    "не подав",
    "не пош",
    "передумал",
    "отказался",
    "отказалась",
    "отказались",
)
_NOT_SUBMITTED_EN = ("not submitted", "did not apply")
_REFUSAL = ("отказ", "refus", "reject", "denied")
_VISA = ("виз", "одобр", "выдал", "мульти", "многократ", "однократ", "двукрат", "visa")
_VISA_EN = ("approved", "granted", "multi", "single", "double")


def parse_outcome(cell: Cell) -> Outcome:
    """The result column, one of hundreds of variants («мульти на 2 года», «отказ»,
    «виза после апелляции», «не подались») → outcome, entries and the appeal mark."""
    text = normalize(cell_text(cell) or "")
    appeal = "апелляц" in text or "appeal" in text
    if any(w in text for w in _NOT_SUBMITTED + _NOT_SUBMITTED_EN):
        return Outcome(VisaOutcome.NOT_SUBMITTED)
    if any(w in text for w in _REFUSAL):
        return Outcome(VisaOutcome.REFUSAL, after_appeal=appeal)
    if any(w in text for w in _VISA + _VISA_EN):
        entries = None
        if any(w in text for w in ("мульти", "многократ", "multi")):
            entries = VisaEntries.MULTIPLE
        elif any(w in text for w in ("двукрат", "double")):
            entries = VisaEntries.DOUBLE
        elif any(w in text for w in ("однократ", "одноразов", "single")):
            entries = VisaEntries.SINGLE
        return Outcome(VisaOutcome.VISA, entries, appeal)
    raise ValueError("unknown result")


# --- Contacts ----------------------------------------------------------------------------

# Belarusian mobile operators and Minsk: local numbers of the first specialist's clients.
_BY_CODES = ("25", "29", "33", "44", "17")


def parse_phone(cell: Cell) -> str:
    """E.164. With the country code as typed, or a Belarusian local number: «8 029 …»,
    «029 …», «29 …» become +375 …"""
    raw = cell_text(cell) or ""
    digits = re.sub(r"\D", "", raw)
    if not raw.lstrip().startswith(("+", "00")):
        if len(digits) == 11 and digits.startswith("80"):
            return f"+375{digits[2:]}"
        if len(digits) == 10 and digits.startswith("0") and digits[1:3] in _BY_CODES:
            return f"+375{digits[1:]}"
        if len(digits) == 9 and digits.startswith(_BY_CODES):
            return f"+375{digits}"
    return normalize_phone(raw)


_TELEGRAM_LINK = re.compile(r"(?:https?://)?(?:t\.me|telegram\.me)/([A-Za-z0-9_]{3,32})", re.I)
# Not the «@» of an email address.
_TELEGRAM_AT = re.compile(r"(?<![\w.])@([A-Za-z0-9_]{3,32})")
_USERNAME = re.compile(r"^[A-Za-z][A-Za-z0-9_]{4,31}$")
_PHONE_LIKE = re.compile(r"\+?\d[\d\s()\- ]{6,}\d")


def parse_telegram(cell: Cell) -> str:
    """A nick (without «@» or the t.me link) or a display name, as the card keeps it."""
    text = (cell_text(cell) or "").strip()
    if match := _TELEGRAM_LINK.search(text):
        return match.group(1)
    text = text.removeprefix("@").strip()
    if not text or len(text) > 64:
        raise ValueError("not a telegram name")
    return text


def parse_contacts(cell: Cell) -> tuple[str | None, str | None]:
    """A «Контакты» cell with a phone and/or a Telegram nick: «+375 29 123-45-67 @anna»."""
    text = cell_text(cell) or ""
    telegram = None
    if match := _TELEGRAM_LINK.search(text) or _TELEGRAM_AT.search(text):
        telegram = match.group(1)
        text = text[: match.start()] + " " + text[match.end() :]
    phone = None
    for candidate in _PHONE_LIKE.findall(text):
        try:
            phone = parse_phone(candidate)
            text = text.replace(candidate, " ")
            break
        except ValueError:
            continue
    if telegram is None and _USERNAME.match(text.strip()):
        telegram = text.strip()
    if phone is None and telegram is None:
        raise ValueError("no phone or telegram")
    return phone, telegram


_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def parse_email(cell: Cell) -> str:
    text = (cell_text(cell) or "").strip()
    if len(text) > 320 or not _EMAIL.match(text):
        raise ValueError("not an email")
    return text


def split_list(cell: Cell) -> list[str]:
    """«Польша, Италия» → two values."""
    return [part.strip() for part in re.split(r"[,;/\n]+", cell_text(cell) or "") if part.strip()]
