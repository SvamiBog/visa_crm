"""What a spreadsheet column can become (backlog 1.14): the fields of each import kind,
their value types and the headers the specialist may have given them. A header that is
not here is mapped by hand once and remembered (app/models/data_import.py).
"""

from dataclasses import dataclass
from enum import StrEnum

from app.core.import_values import normalize
from app.models.data_import import ImportKind


class ValueType(StrEnum):
    NAME = "name"
    TEXT = "text"
    PHONE = "phone"
    TELEGRAM = "telegram"
    CONTACTS = "contacts"  # a phone and/or a Telegram nick in one cell
    EMAIL = "email"
    DATE = "date"
    INT = "int"
    MONEY = "money"
    BOOL = "bool"
    DECISION = "decision"
    OUTCOME = "outcome"
    COUNTRY = "country"
    COUNTRIES = "countries"
    CHANNEL = "channel"
    PARTNER = "partner"
    PURPOSE = "purpose"
    REGISTRAR = "registrar"
    CITY = "city"
    SECRET = "secret"  # a password: goes to the vault encrypted, never shown back


# Value types looked up in a list; an unknown value is resolved by the specialist.
LOOKUPS = (
    ValueType.CHANNEL,
    ValueType.PARTNER,
    ValueType.PURPOSE,
    ValueType.REGISTRAR,
    ValueType.COUNTRY,
    ValueType.CITY,
)
# Lists the import may add an entry to; countries and cities are shared reference data.
CREATABLE = (ValueType.CHANNEL, ValueType.PARTNER, ValueType.PURPOSE, ValueType.REGISTRAR)

K = ImportKind
ALL = frozenset(ImportKind)
WITH_CASE = frozenset({K.CASES, K.QUEUE, K.ATTEMPTS})


@dataclass(frozen=True)
class ImportField:
    key: str
    type: ValueType
    kinds: frozenset[ImportKind]
    synonyms: tuple[str, ...] = ()
    # A module the workspace needs besides clients (and cases for case kinds).
    module: str | None = None
    max_length: int | None = None
    required_in: frozenset[ImportKind] = frozenset()


def _f(key: str, type_: ValueType, kinds, *synonyms: str, **extra) -> ImportField:
    return ImportField(key, type_, frozenset(kinds), synonyms, **extra)


V = ValueType
FIELDS: tuple[ImportField, ...] = (
    # The person: found or created in the client index.
    _f(
        "full_name",
        V.NAME,
        ALL,
        "фио",
        "имя",
        "клиент",
        "заявитель",
        "фамилия имя",
        "имя фамилия",
        "фио клиента",
        "name",
        "full name",
        max_length=200,
        required_in=ALL,
    ),
    _f(
        "full_name_latin",
        V.TEXT,
        ALL,
        "фио латиницей",
        "латиницей",
        "имя латиницей",
        "latin",
        max_length=200,
    ),
    _f("phone", V.PHONE, ALL, "телефон", "тел", "номер телефона", "мобильный", "phone"),
    _f("telegram", V.TELEGRAM, ALL, "telegram", "телеграм", "телеграмм", "тг", "tg", "ник"),
    _f("contacts", V.CONTACTS, ALL, "контакты", "контакт", "связь", "contacts"),
    _f("email", V.EMAIL, ALL, "email", "e mail", "почта клиента", "имейл", "емейл"),
    _f("birth_date", V.DATE, ALL, "др", "дата рождения", "день рождения", "birthday"),
    _f("channel", V.CHANNEL, ALL, "канал", "канал прихода", "откуда", "источник", "channel"),
    _f("partner", V.PARTNER, ALL, "партнер", "от партнера", "partner"),
    _f("contact_note", V.TEXT, ALL, "заметка", "примечание", "о клиенте", max_length=5000),
    # Консультации.
    _f(
        "held_on",
        V.DATE,
        {K.CONSULTATIONS},
        "дата консультации",
        "дата",
        "консультация",
        "дата обращения",
        required_in=frozenset({K.CONSULTATIONS}),
    ),
    _f(
        "applicants",
        V.INT,
        {K.CONSULTATIONS},
        "количество заявителей",
        "кол во заявителей",
        "заявителей",
        "сколько человек",
        "человек",
    ),
    _f("decision", V.DECISION, {K.CONSULTATIONS}, "решение", "решение по визе"),
    _f("destinations", V.COUNTRIES, {K.CONSULTATIONS}, "страна", "страны", "направление", "куда"),
    _f(
        "fee_amount",
        V.MONEY,
        {K.CONSULTATIONS},
        "оплата консультации",
        "стоимость консультации",
        "оплата",
    ),
    _f("fee_paid", V.BOOL, {K.CONSULTATIONS}, "оплачено", "оплачена"),
    _f(
        "consultation_notes",
        V.TEXT,
        {K.CONSULTATIONS},
        "кейс",
        "заметки",
        "вводные",
        "комментарий",
        max_length=5000,
    ),
    # The case.
    _f(
        "destination",
        V.COUNTRY,
        {K.CASES, K.BOOKINGS},
        "страна",
        "страна визы",
        "направление",
        "country",
        required_in=frozenset({K.CASES}),
    ),
    _f("purpose", V.PURPOSE, WITH_CASE, "цель", "цель визы", "категория", "purpose"),
    _f("city", V.CITY, WITH_CASE, "город", "город подачи", "где подается"),
    _f("registrar", V.REGISTRAR, {K.CASES, K.QUEUE}, "регистратор"),
    _f(
        "vac_number",
        V.TEXT,
        WITH_CASE,
        "id",
        "id в вц",
        "номер в вц",
        "номер анкеты",
        "анкета",
        max_length=64,
    ),
    _f("application_email", V.EMAIL, WITH_CASE, "почта для записи", "почта заявки"),
    _f(
        "queued_on",
        V.DATE,
        {K.CASES, K.QUEUE},
        "дата постановки",
        "постановка",
        "дата постановки в очередь",
        "в очереди с",
        required_in=frozenset({K.QUEUE}),
    ),
    _f("letter_on", V.DATE, {K.CASES, K.QUEUE}, "письмо", "письмо с датой", "дата письма"),
    _f("removed_on", V.DATE, {K.QUEUE}, "снятие", "снят", "дата снятия", "снялись"),
    _f("removed_reason", V.TEXT, {K.QUEUE}, "причина снятия", max_length=500),
    _f("submission_on", V.DATE, WITH_CASE, "подача", "дата подачи", "дата записи", "запись"),
    _f("outcome", V.OUTCOME, {K.CASES}, "результат", "итог"),
    _f("decided_on", V.DATE, {K.CASES}, "дата решения", "дата результата"),
    _f("visa_valid_from", V.DATE, {K.CASES}, "виза с", "действует с"),
    _f("visa_valid_to", V.DATE, {K.CASES}, "виза до", "действует до", "срок визы"),
    _f(
        "price",
        V.MONEY,
        {K.CASES},
        "стоимость",
        "цена",
        "сумма",
        "стоимость услуги",
        module="finances",
    ),
    _f("paid", V.MONEY, {K.CASES}, "оплачено", "оплата", "внесено", module="finances"),
    _f("paid_on", V.DATE, {K.CASES}, "дата оплаты", module="finances"),
    # Заявки с окном (France).
    _f(
        "sent_on",
        V.DATE,
        {K.ATTEMPTS},
        "дата заявки",
        "заявка",
        "дата отправки",
        required_in=frozenset({K.ATTEMPTS}),
    ),
    _f("deadline_on", V.DATE, {K.ATTEMPTS}, "60 й день", "60 день", "дедлайн", "крайний срок"),
    _f("repeats", V.INT, {K.ATTEMPTS}, "повторы", "повтор", "попытки"),
    # Брони.
    _f("place", V.TEXT, {K.BOOKINGS}, "отель", "гостиница", "hotel", max_length=200),
    _f("starts_on", V.DATE, {K.BOOKINGS}, "заезд", "дата заезда"),
    _f("ends_on", V.DATE, {K.BOOKINGS}, "выезд", "дата выезда"),
    _f(
        "free_cancel_until",
        V.DATE,
        {K.BOOKINGS},
        "бесплатная отмена",
        "отмена до",
        "крайняя дата отмены",
        "дедлайн отмены",
    ),
    _f("reference", V.TEXT, {K.BOOKINGS}, "номер брони", "бронь", max_length=64),
    _f(
        "booking_account",
        V.TEXT,
        {K.BOOKINGS},
        "аккаунт",
        "аккаунт booking",
        max_length=320,
        module="vault",
    ),
    _f("booking_note", V.TEXT, {K.BOOKINGS}, "комментарий", "число броней", max_length=500),
    # Accounts: to the vault, passwords encrypted.
    _f("email_login", V.TEXT, WITH_CASE, "почта", "логин почты", max_length=320, module="vault"),
    _f(
        "email_password",
        V.SECRET,
        WITH_CASE,
        "пароль",
        "пароль почты",
        "пароль от почты",
        max_length=200,
        module="vault",
    ),
    _f(
        "site_login",
        V.TEXT,
        WITH_CASE,
        "логин",
        "логин вц",
        "логин на сайте",
        max_length=320,
        module="vault",
    ),
    _f(
        "site_password",
        V.SECRET,
        WITH_CASE,
        "пароль вц",
        "пароль на сайте",
        "пароль сайта",
        max_length=200,
        module="vault",
    ),
)
BY_KEY = {f.key: f for f in FIELDS}


def kind_modules(kind: ImportKind) -> tuple[str, ...]:
    """Consultations need the client index; everything else also needs cases."""
    return ("clients",) if kind == ImportKind.CONSULTATIONS else ("clients", "cases")


def fields_for(kind: ImportKind, modules: set[str] | list[str]) -> list[ImportField]:
    return [f for f in FIELDS if kind in f.kinds and (f.module is None or f.module in modules)]


def guess(headers: list[str], fields: list[ImportField]) -> list[str | None]:
    """A field for each header by its usual names; each field at most once."""
    by_synonym = {normalize(s): f.key for f in fields for s in f.synonyms}
    taken: set[str] = set()
    result: list[str | None] = []
    for header in headers:
        key = by_synonym.get(normalize(header))
        if key is not None and key not in taken:
            taken.add(key)
            result.append(key)
        else:
            result.append(None)
    return result
