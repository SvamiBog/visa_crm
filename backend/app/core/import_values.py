"""Cells of the specialist's spreadsheets (backlog 1.14, 1.16), typed by hand over the
years: dates in several formats, sums with a currency, «да» or «+», a visa result in
words, a phone and a Telegram nick in one cell, a note after a name, «почта пароль». Pure
functions; a cell that does not parse raises ValueError (CellError with a reason) and
becomes a warning in the import report. A value read with a guess — a date without its
year — comes with the reason, so the report says so.
"""

import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation

from app.core.finance import money
from app.core.phone import normalize_phone
from app.models.consultation import Decision
from app.models.visa_case import VisaEntries, VisaOutcome

Cell = str | int | float | Decimal | bool | date | datetime | None

_NON_WORD = re.compile(r"[^\w]+")


class CellError(ValueError):
    """A cell that does not parse, with the reason for the report (import.reason.*)."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


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


# What people type instead of leaving a cell empty.
_NOTHING = {"нет", "нету", "no", "none", "n a", "na", "не знаю", "неизвестно"}


def is_blank(value: str, words: bool = True) -> bool:
    """«-», «—», «?» and (with words) «нет»: nothing in the cell, as if it were empty."""
    return not any(ch.isalnum() for ch in value) or (words and normalize(value) in _NOTHING)


def normalize(value: str) -> str:
    """For comparing names and headers: case, «ё» and punctuation do not matter."""
    return " ".join(_NON_WORD.sub(" ", value.casefold().replace("ё", "е")).split())


def name_key(value: str) -> str:
    """A person's name for matching rows: «Иванова Анна» and «анна иванова» are one."""
    return " ".join(sorted(normalize(value).split()))


_NAME_PARENS = re.compile(r"\s*\(([^)]*)\)?")
_NAME_FROM = re.compile(r"\s+(от\s+\S.*)$", re.IGNORECASE)
# Marks that only say the person came again: the import finds them by the name anyway.
_AGAIN = {"повторно", "повторная", "повторный", "повтор", "снова"}
_GROUP = re.compile(r"\s*(?:\+|&|\sи\s)\s*")


def split_name(value: str) -> tuple[str, str | None]:
    """«Иванова Анна (коллега Ольги)», «Иванова Анна от Ольги Т.» → the name and the note
    after it: the person is found by the name, the note goes to the card."""
    notes = [n.strip() for n in _NAME_PARENS.findall(value)]
    name = _NAME_PARENS.sub(" ", value)
    if match := _NAME_FROM.search(name):
        notes.append(match.group(1).strip())
        name = name[: match.start()]
    notes = [n for n in notes if n and normalize(n) not in _AGAIN]
    return " ".join(name.split()).strip(" ,;-–—"), "; ".join(notes) or None


def group_names(name: str) -> list[str]:
    """«Иванова Анна + супруг + сын», «Анна и Олег»: several people in one cell (they are
    not split into cards — open-questions.md, 19); one name gives a list of one."""
    return [part for part in _GROUP.split(name) if part.strip()]


# --- Dates and numbers ----------------------------------------------------------------

# «12.06.2026», «12/06/26», «17.12 2026» (a space before a four-digit year).
_DMY = re.compile(r"^(\d{1,2})[./-](\d{1,2})(?:[./-](\d{4}|\d{2})|\s+(\d{4}))(?:\D.*)?$")
_YMD = re.compile(r"^(\d{4})-(\d{1,2})-(\d{1,2})(?:\D.*)?$")
# «17.08», «17.08 в 15.57»: no year.
_DM = re.compile(r"^(\d{1,2})[./](\d{1,2})(?:\s+\D.*)?$")
# «октябрь 2024».
_MONTH_YEAR = re.compile(r"^([^\W\d_]+)\.?\s+(\d{4})(?:\D.*)?$")
# «до 25.12.2026», «с 08.06»: the word before the date.
_DATE_PREFIX = re.compile(r"^(?:до|по|с|c|от|около|примерно)\s+", re.IGNORECASE)
_NUMBER_TEXT = re.compile(r"^\d+(?:[.,]0+)?$")
# Days since 30.12.1899: how Excel and Google Sheets keep a date, and how it comes out
# when the cell is formatted as a number or the sheet is saved as text. 1920 to 2099.
_EXCEL_EPOCH = date(1899, 12, 30)
EXCEL_DAYS = range(7306, 73050)
_MONTHS = (
    ("январ", "янв", "january", "jan"),
    ("феврал", "фев", "february", "feb"),
    ("март", "мар", "march", "mar"),
    ("апрел", "апр", "april", "apr"),
    ("май", "мая", "мае", "may"),
    ("июн", "june", "jun"),
    ("июл", "july", "jul"),
    ("август", "авг", "august", "aug"),
    ("сентябр", "сен", "september", "sep"),
    ("октябр", "окт", "october", "oct"),
    ("ноябр", "ноя", "november", "nov"),
    ("декабр", "дек", "december", "dec"),
)


def parse_month(cell: Cell) -> int:
    """«Октябрь», «окт.», «October» → 10."""
    word = normalize(cell_text(cell) or "")
    for number, stems in enumerate(_MONTHS, start=1):
        if word and " " not in word and any(word.startswith(stem) for stem in stems):
            # «мая» is May, «март» is March: May's words are whole.
            if number == 5 and word not in stems:
                continue
            return number
    raise CellError("bad_month")


def _excel_day(cell: Cell) -> date | None:
    """A date kept as its number of days (46300 → 05.10.2026), or None."""
    if isinstance(cell, bool):
        return None
    if isinstance(cell, int | float | Decimal):
        number = Decimal(repr(cell)) if isinstance(cell, float) else Decimal(cell)
    elif isinstance(cell, str) and _NUMBER_TEXT.match(cell.strip()):
        number = Decimal(cell.strip().replace(",", "."))
    else:
        return None
    if number != number.to_integral_value() or int(number) not in EXCEL_DAYS:
        return None
    return _EXCEL_EPOCH + timedelta(days=int(number))


def parse_date(cell: Cell, today: date) -> date:
    """A date cell, a date kept as Excel's number of days, or text «12.06.2026»,
    «12/06/26», «17.12 2026», «2026-06-12» (day first). A two-digit year is this century
    unless that is more than 10 years ahead: «85» is 1985."""
    if isinstance(cell, datetime):
        day = cell.date()
    elif isinstance(cell, date):
        day = cell
    elif (excel := _excel_day(cell)) is not None:
        day = excel
    elif isinstance(cell, str):
        text = cell.strip()
        if match := _YMD.match(text):
            year, month, dom = (int(g) for g in match.groups())
        elif match := _DMY.match(text):
            dom, month = int(match.group(1)), int(match.group(2))
            year = int(match.group(3) or match.group(4))
            if match.group(3) and len(match.group(3)) == 2:
                year += 2000 if 2000 + year <= today.year + 10 else 1900
        else:
            raise ValueError("not a date")
        day = date(year, month, dom)
    else:
        raise ValueError("not a date")
    if not 1900 <= day.year <= 2100:
        raise ValueError("date out of range")
    return day


def parse_loose_date(cell: Cell, today: date, year: int | None) -> tuple[date, str | None]:
    """A date as parse_date, also «до 25.12.2026»; and with a guess the report shows:
    «17.08» or «17.08 в 15.57» — in the table's year (from the sheet's name, else this
    year), «октябрь 2024» — the 1st of the month. A bare year is not a date."""
    text = _DATE_PREFIX.sub("", cell.strip()) if isinstance(cell, str) else cell
    try:
        return parse_date(text, today), None
    except ValueError:
        pass
    if isinstance(text, str) and (match := _DM.match(text)):
        try:
            day = date(year or today.year, int(match.group(2)), int(match.group(1)))
        except ValueError as error:
            raise CellError("bad_date") from error
        return day, "date_without_year"
    if isinstance(text, str) and (match := _MONTH_YEAR.match(text)):
        try:
            month = parse_month(match.group(1))
        except CellError as error:
            raise CellError("bad_date") from error
        if 1900 <= int(match.group(2)) <= 2100:
            return date(int(match.group(2)), month, 1), "date_month_only"
    number = cell_text(text) or ""
    if _NUMBER_TEXT.match(number) and 1900 <= int(float(number.replace(",", "."))) <= 2100:
        raise CellError("year_only")
    raise CellError("bad_date")


_NOT_SENT_WORDS = ("не став", "не буд", "не пода", "не отправ", "стоп")


def is_not_sent(value: str) -> bool:
    """«не ставим» in a «Повторно» column: the application is not sent again."""
    word = normalize(value)
    return word.startswith(_NOT_SENT_WORDS) or word in ("нет", "отказ")


_REMOVED = re.compile(
    r"удал\w*\s+(?:из\s+)?(?:очеред|вейт|лист|wait)\w*(?P<rest>.*)$", re.IGNORECASE | re.DOTALL
)
_DATE_IN_TEXT = re.compile(r"\d{1,2}[./]\d{1,2}(?:[./](?:\d{4}|\d{2})(?!\d)|\s+\d{4})?")


def removal_date(value: str, today: date, year: int | None) -> tuple[date, str | None] | None:
    """«Удалена из очереди 12.06.2026», «удалили из вейта 12.06» in a comment: the day the
    entry ended (None — the comment says nothing of a removal)."""
    match = _REMOVED.search(value)
    if match is None:
        return None
    if found := _DATE_IN_TEXT.search(match.group("rest")):
        return parse_loose_date(found.group(0), today, year)
    raise CellError("removal_without_date")


_YEAR = re.compile(r"(?<!\d)(20\d{2})(?!\d)")
# «Подача25», «ЖО26»: two digits right after a word.
_SHORT_YEAR = re.compile(r"[^\W\d_](\d{2})(?!\d)")


def table_year(*names: str) -> int | None:
    """The year a sheet is about, from its name: «Подача25», «2024 Подача», «ЖО26»."""
    for name in names:
        if match := _YEAR.search(name or ""):
            return int(match.group(1))
    for name in names:
        if match := _SHORT_YEAR.search(name or ""):
            return 2000 + int(match.group(1))
    return None


def parse_int(cell: Cell) -> int:
    """A whole number; text may go on after it: «2 человека». «2.0» — a number saved as
    text — is 2."""
    if isinstance(cell, bool):
        raise ValueError("not a number")
    if isinstance(cell, int):
        return cell
    if isinstance(cell, float | Decimal) and cell == int(cell):
        return int(cell)
    if isinstance(cell, str) and _NUMBER_TEXT.match(cell.strip()):
        return int(float(cell.strip().replace(",", ".")))
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
    «029 …», «29 …» become +375 …; «375291234567.0» is a number saved as text."""
    raw = cell_text(cell) or ""
    if _NUMBER_TEXT.match(raw):
        raw = re.sub(r"[.,]0+$", "", raw)
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


def is_nick(value: str | None) -> bool:
    """A Telegram username, not a display name or a remark: only these tell people apart
    («Анна», «в вайбере» may be anyone)."""
    return bool(value) and _USERNAME.match(value or "") is not None


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


_URL = re.compile(r"^(?:https?://|www\.)", re.IGNORECASE)
_LABELS = {"логин", "login", "пароль", "пароли", "password", "pass", "почта", "email", "mail"}


def split_credentials(value: str) -> tuple[str, str | None]:
    """«anna@mail.ru Pa55word», «логин: ITA123 пароль: x1» → the login and the password
    typed after it (None if there is none). Links and labels are dropped; the login is
    the email in the cell, else its first word."""
    words = [
        w
        for w in value.split()
        if not _URL.match(w) and normalize(w) not in _LABELS and not is_blank(w)
    ]
    if not words:
        return value.strip(), None
    login = next((w for w in words if _EMAIL.match(w.rstrip(",;"))), words[0])
    rest = [w for w in words if w is not login]
    return login.rstrip(",;"), " ".join(rest) or None


def looks_like_credentials(value: str) -> bool:
    """A login with a password after it, or a cell that says «пароль»: not shown back."""
    words = value.split()
    if any(w.casefold().startswith(("парол", "pass")) for w in words):
        return True
    return len(words) > 1 and _EMAIL.match(words[0].rstrip(",;")) is not None


# An ID at a visa center inside other text: «URN: ITA123456789012», «PLB1234567890».
_REFERENCE = re.compile(r"\b[A-Z]{2,4}\d{6,}\b")


def parse_reference(cell: Cell) -> str:
    """The case's number at the visa center. An email is not a number; the number is
    taken out of the text around it."""
    text = (cell_text(cell) or "").strip()
    if _NUMBER_TEXT.match(text):
        text = re.sub(r"[.,]0+$", "", text)  # «12345.0» from a sheet saved as text
    if _EMAIL.match(text):
        raise CellError("email_not_number")
    if " " in text and (match := _REFERENCE.search(text)):
        return match.group(0)
    if len(text) > 64:
        raise CellError("too_long")
    return text


# Who puts the client in the queue when it is not a registrar: the specialist or the
# client themselves. No registrar, no talon.
_SELF_WORDS = {"я", "сама", "сам", "сами", "самостоятельно", "самостоятельная"}


def is_self_registered(value: str) -> bool:
    """«я», «самостоятельно», «я помогала», «клиент сам», «без посредника»."""
    words = normalize(value).split()
    return bool(words) and (
        words[0] in _SELF_WORDS
        or words[-1] in _SELF_WORDS
        or words[0].startswith("самостоят")
        or words[:2] in (["без", "посредника"], ["без", "регистратора"])
    )


def split_list(cell: Cell) -> list[str]:
    """«Польша, Италия» → two values."""
    return [part.strip() for part in re.split(r"[,;/\n]+", cell_text(cell) or "") if part.strip()]
