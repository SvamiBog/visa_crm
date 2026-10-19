"""What a spreadsheet column can become (backlog 1.14, 1.16): the fields of each import
kind, their value types and the headers the specialist may have given them. A header that
is not here is mapped by hand once and remembered (app/models/data_import.py) — each
specialist's tables are their own.
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
    MONTH = "month"  # «Месяц обращения»: the date when the day is missing
    RESEND = "resend"  # a windowed application sent again: a date, or «не ставим»
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
    REFERENCE = "reference"  # the number at the visa center
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
    # Longer text is cut to max_length with a warning (notes), not dropped.
    truncate: bool = False
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
    _f(
        "phone",
        V.PHONE,
        ALL,
        "телефон",
        "тел",
        "номер телефона",
        "мобильный",
        "телефон при регистрации",
        "телефон клиента",
        "phone",
    ),
    _f("telegram", V.TELEGRAM, ALL, "telegram", "телеграм", "телеграмм", "тг", "tg", "ник"),
    _f("contacts", V.CONTACTS, ALL, "контакты", "контакт", "связь", "contacts"),
    _f(
        "email",
        V.EMAIL,
        ALL,
        "email",
        "e mail",
        "почта клиента",
        "email клиента",
        "имейл",
        "емейл",
    ),
    _f("birth_date", V.DATE, ALL, "др", "дата рождения", "день рождения", "birthday"),
    _f("channel", V.CHANNEL, ALL, "канал", "канал прихода", "откуда", "источник", "channel"),
    _f("partner", V.PARTNER, ALL, "партнер", "от партнера", "partner"),
    _f(
        "contact_note",
        V.TEXT,
        ALL,
        "заметка",
        "примечание",
        "о клиенте",
        max_length=5000,
        truncate=True,
    ),
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
    _f("held_month", V.MONTH, {K.CONSULTATIONS}, "месяц обращения", "месяц"),
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
    _f("decision", V.DECISION, {K.CONSULTATIONS}, "решение", "решение по визе", "делаем визу"),
    _f("destinations", V.COUNTRIES, {K.CONSULTATIONS}, "страна", "страны", "направление", "куда"),
    _f(
        "fee_amount",
        V.MONEY,
        {K.CONSULTATIONS},
        "оплата консультации",
        "стоимость консультации",
        "оплата",
        "стоимость",
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
        truncate=True,
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
    _f("registrar", V.REGISTRAR, {K.CASES, K.QUEUE}, "регистратор", "посредник"),
    _f(
        "vac_number",
        V.REFERENCE,
        WITH_CASE,
        "id",
        "id в вц",
        "номер в вц",
        "номер анкеты",
        "анкета",
        "присвоенный номер",
        "номер",
        # Greece: the username at the visa center (country rules → identifier type).
        "код пользователя",
        "код пользователя греция",
        "код кабинета",
        max_length=200,
    ),
    _f("application_email", V.EMAIL, WITH_CASE, "почта для записи", "почта заявки"),
    _f(
        "queued_on",
        V.DATE,
        {K.CASES, K.QUEUE},
        "дата постановки",
        "постановка",
        "дата постановки в очередь",
        "постановка в очередь",
        "в очереди с",
        required_in=frozenset({K.QUEUE}),
    ),
    # Put in the queue again — Italy's new visa center: the first entry ends that day.
    _f(
        "requeued_on",
        V.DATE,
        {K.QUEUE},
        "дата постановки в очередь в новом вц",
        "постановка в новом вц",
        "повторная постановка",
    ),
    _f(
        "letter_on",
        V.DATE,
        {K.CASES, K.QUEUE},
        "письмо",
        "письмо с датой",
        "дата письма",
        # Poland: «Дата записи» is the day the appointment date came.
        "дата записи",
        "дата получения письма",
        "дата получения письма с датой подачи",
    ),
    _f("removed_on", V.DATE, {K.QUEUE}, "снятие", "снят", "дата снятия", "снялись"),
    _f("removed_reason", V.TEXT, {K.QUEUE}, "причина снятия", max_length=500, truncate=True),
    # «Удалена из очереди 12.06.2026» ends the entry; other remarks go to the card.
    _f(
        "queue_note",
        V.TEXT,
        {K.QUEUE},
        "комментарий",
        "комментарии",
        "примечание",
        max_length=5000,
        truncate=True,
    ),
    _f("submission_on", V.DATE, WITH_CASE, "дата подачи", "подача"),
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
        "стоимость в рублях",
        module="finances",
    ),
    _f(
        "paid",
        V.MONEY,
        {K.CASES},
        "оплачено",
        "оплата",
        "внесено",
        "предоплата",
        module="finances",
    ),
    _f("paid_on", V.DATE, {K.CASES}, "дата оплаты", module="finances"),
    # The talon: to the case's registrar, already paid (decisions.md → «Импорт таблиц»).
    _f(
        "registrar_fee",
        V.MONEY,
        {K.CASES, K.QUEUE},
        "оплата за талон",
        "талон",
        "стоимость талона",
        "мой расходник",
        "расходник",
        "оплата моя посредникам",
        "оплата посредникам",
        "оплата посреднику",
        "оплата регистратору",
        module="finances",
    ),
    # What the partner who brought the client was paid: a fixed payout, already paid.
    _f(
        "partner_payout",
        V.MONEY,
        {K.CASES},
        "оплата процента",
        "процент",
        "процент партнеру",
        "выплата партнеру",
        "оплата партнеру",
        module="finances",
    ),
    # Заявки с окном (France).
    _f(
        "sent_on",
        V.DATE,
        {K.ATTEMPTS},
        "дата заявки",
        "заявка",
        "дата отправки",
        "дата отправки заявки",
        required_in=frozenset({K.ATTEMPTS}),
    ),
    _f(
        "deadline_on",
        V.DATE,
        {K.ATTEMPTS},
        "60й день",
        "60 й день",
        "60 день",
        "дедлайн",
        "крайний срок",
    ),
    # Sent again after the window ran out: «Повторно», «60й день (2-й цикл)», …
    _f("sent_on_2", V.RESEND, {K.ATTEMPTS}, "повторно", "повторная заявка"),
    _f("deadline_on_2", V.DATE, {K.ATTEMPTS}, "60й день 2 й цикл", "60 й день 2 й цикл"),
    _f("sent_on_3", V.RESEND, {K.ATTEMPTS}, "повторно 2 й цикл"),
    _f("deadline_on_3", V.DATE, {K.ATTEMPTS}, "60й день 3 й цикл", "60 й день 3 й цикл"),
    _f("sent_on_4", V.RESEND, {K.ATTEMPTS}, "повторно 3 й цикл"),
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
        "бесплатная отмена до",
        "отмена до",
        "отменить бронирование до",
        "отмена брони до",
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
        "почта букинга",
        "почта booking",
        max_length=320,
        module="vault",
    ),
    _f(
        "booking_note",
        V.TEXT,
        {K.BOOKINGS},
        "комментарий",
        "число броней",
        max_length=500,
        truncate=True,
    ),
    # Accounts: to the vault, passwords encrypted. A login cell may hold the password
    # after it: «anna@mail.ru Pa55word».
    _f("email_login", V.TEXT, WITH_CASE, "почта", "логин почты", max_length=320, module="vault"),
    # A mailbox the specialist made for the client: the account is marked so.
    _f(
        "own_email_login",
        V.TEXT,
        WITH_CASE,
        "email создала я",
        "почта создала я",
        "созданная почта",
        "почта создана мной",
        max_length=320,
        module="vault",
    ),
    _f(
        "email_password",
        V.SECRET,
        WITH_CASE,
        "пароль",
        "пароли",
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
        "данные кабинета",
        "новый вц данные кабинета",
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
    """A field for each header by its usual names; each field at most once. When two
    headers name one field, the field's more usual name wins (listed earlier), then the
    left column: «Дата подачи» before «Подача»."""
    by_synonym = {normalize(s): (f.key, rank) for f in fields for rank, s in enumerate(f.synonyms)}
    best: dict[str, tuple[int, int]] = {}
    for index, header in enumerate(headers):
        if (found := by_synonym.get(normalize(header))) is not None:
            key, rank = found
            if key not in best or (rank, index) < best[key]:
                best[key] = (rank, index)
    chosen = {index: key for key, (_, index) in best.items()}
    return [chosen.get(index) for index in range(len(headers))]
