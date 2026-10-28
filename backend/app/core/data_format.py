"""The VisaCRM data format (backlog 1.20, docs/data-format.md): one XLSX workbook that the
import takes and the export gives. Its sheets and columns in Russian and English, what each
column holds and which ones are required. The reader (app/core/workbook.py), the import
(app/core/data_import.py), the template and the prompt's list of columns are built from
here, so they cannot drift apart.

A column is a key (the row's own code or a code of another sheet's row), data, or a
reference column («Клиент», «Номер кейса», «Остаток»): filled by the export so the file can
be worked with without lookups, never loaded — the client's name is only compared with the
code, to catch a shifted one.
"""

from dataclasses import dataclass
from enum import StrEnum

VERSION = 1
# A code: anything up to this long, unique on its sheet within the file.
CODE_LENGTH = 64


class ValueType(StrEnum):
    CODE = "code"
    TEXT = "text"
    DOCUMENT_NUMBER = "document_number"  # passport or personal number: letters and digits
    DATE = "date"
    INT = "int"
    MONEY = "money"
    RATE = "rate"
    PERCENT = "percent"
    CURRENCY = "currency"
    BOOL = "bool"
    PHONE = "phone"
    TELEGRAM = "telegram"
    EMAIL = "email"
    CHOICE = "choice"  # one of the format's fixed words
    COUNTRY = "country"
    COUNTRIES = "countries"
    CITY = "city"  # a visa center's city in the case's country
    # The specialist's own lists: an unknown value may be added to the list.
    CHANNEL = "channel"
    PARTNER = "partner"
    PURPOSE = "purpose"
    REGISTRAR = "registrar"
    SYSTEM = "system"  # a vault system; looked up, never added
    SECRET = "secret"  # a password: to the vault encrypted, never shown back


class ColumnKind(StrEnum):
    KEY = "key"
    DATA = "data"
    REF = "ref"


# Lists the import may add an entry to; countries, cities and systems are looked up only.
CREATABLE = (ValueType.CHANNEL, ValueType.PARTNER, ValueType.PURPOSE, ValueType.REGISTRAR)


@dataclass(frozen=True)
class Choice:
    value: str
    ru: str
    en: str


def _choices(*items: tuple[str, str, str]) -> tuple[Choice, ...]:
    return tuple(Choice(*item) for item in items)


DECISION = _choices(("yes", "да", "yes"), ("no", "нет", "no"), ("thinking", "думает", "thinking"))
STAGE = _choices(
    ("waiting", "ожидание", "waiting"),
    ("documents", "сбор документов", "documents"),
    ("submission", "подача", "submission"),
    ("review", "рассмотрение", "review"),
    ("result", "результат", "result"),
)
OUTCOME = _choices(
    ("visa", "виза", "visa"),
    ("refusal", "отказ", "refusal"),
    ("not_submitted", "не подались", "not submitted"),
)
ENTRIES = _choices(
    ("single", "однократная", "single"),
    ("double", "двукратная", "double"),
    ("multiple", "многократная", "multiple"),
)
HALL = _choices(("standard", "обычный", "standard"), ("premium", "премиум", "premium"))
EMAIL_OWNER = _choices(
    ("client", "клиента", "client's"),
    ("specialist", "создала я", "created by me"),
    ("specialist_backup", "моя резервная", "my backup"),
    ("registrar", "регистратора", "registrar's"),
)
PARTNER_TERMS = _choices(
    ("percent", "процент", "percent"),
    ("fixed", "фикс", "fixed"),
    ("none", "без выплаты", "no payout"),
    ("unspecified", "не учитывается", "not tracked"),
)
ATTEMPT_OUTCOME = _choices(
    ("date_received", "дата получена", "date received"),
    ("expired", "истекла", "expired"),
    ("cancelled", "отменена", "cancelled"),
)
BOOKING_KIND = _choices(("hotel", "отель", "hotel"), ("flight", "авиабронь", "flight"))
BOOKING_STATUS = _choices(
    ("active", "действует", "active"),
    ("rescheduled", "перенесена", "rescheduled"),
    ("cancelled", "отменена", "cancelled"),
)
MESSENGER = _choices(("whatsapp", "WhatsApp", "WhatsApp"), ("viber", "Viber", "Viber"))
# The Cyrillic and the Latin letters look the same: both are read (app/core/data_values.py).
SEX = _choices(("M", "М", "M"), ("F", "Ж", "F"), ("X", "X", "X"))


@dataclass(frozen=True)
class Column:
    key: str
    ru: str
    en: str
    type: ValueType
    kind: ColumnKind = ColumnKind.DATA
    required: bool = False
    max_length: int | None = None
    choices: tuple[Choice, ...] = ()
    # A module the workspace needs for this column besides its sheet's.
    module: str | None = None
    # A code column: the sheet whose rows it points to.
    points_to: str | None = None


@dataclass(frozen=True)
class Sheet:
    key: str
    ru: str
    en: str
    columns: tuple[Column, ...]
    # The module the workspace needs for the sheet.
    module: str

    @property
    def code(self) -> Column:
        """The row's own code: the first column."""
        return self.columns[0]

    def column(self, key: str) -> Column:
        return next(c for c in self.columns if c.key == key)


K, D, R = ColumnKind.KEY, ColumnKind.DATA, ColumnKind.REF
V = ValueType


def _code(ru: str = "Код", en: str = "Code", *, required: bool = False) -> Column:
    return Column("code", ru, en, V.CODE, K, required=required, max_length=CODE_LENGTH)


def _client(*, required: bool = True) -> Column:
    return Column(
        "client",
        "Код клиента",
        "Client code",
        V.CODE,
        K,
        required=required,
        max_length=CODE_LENGTH,
        points_to="clients",
    )


def _case(*, required: bool = True) -> Column:
    return Column(
        "case",
        "Код кейса",
        "Case code",
        V.CODE,
        K,
        required=required,
        max_length=CODE_LENGTH,
        points_to="cases",
    )


CLIENT_NAME = Column("client_name", "Клиент", "Client", V.TEXT, R)
COUNTRY_REF = Column("country", "Страна", "Country", V.TEXT, R)


def _text(key: str, ru: str, en: str, length: int, **extra) -> Column:
    return Column(key, ru, en, V.TEXT, max_length=length, **extra)


def _date(key: str, ru: str, en: str, **extra) -> Column:
    return Column(key, ru, en, V.DATE, **extra)


def _choice(key: str, ru: str, en: str, choices: tuple[Choice, ...], **extra) -> Column:
    return Column(key, ru, en, V.CHOICE, choices=choices, **extra)


SHEETS: tuple[Sheet, ...] = (
    Sheet(
        "clients",
        "Клиенты",
        "Clients",
        (
            _code("Код клиента", "Client code", required=True),
            _text("last_name", "Фамилия", "Last name", 200),
            _text("first_name", "Имя", "First name", 200),
            _text("middle_name", "Отчество", "Middle name", 200),
            _text("last_name_latin", "Фамилия латиницей", "Last name in Latin", 200),
            _text("first_name_latin", "Имя латиницей", "First name in Latin", 200),
            Column("phone", "Телефон", "Phone", V.PHONE),
            Column("telegram", "Telegram", "Telegram", V.TELEGRAM),
            _choice("messenger", "Мессенджер", "Messenger", MESSENGER),
            _text("messenger_contact", "Контакт в мессенджере", "Messenger contact", 64),
            Column("email", "Email", "Email", V.EMAIL),
            _date("birth_date", "Дата рождения", "Date of birth"),
            Column("channel", "Канал прихода", "Lead channel", V.CHANNEL),
            Column("partner", "Партнёр", "Partner", V.PARTNER),
            _date("consent_signed_on", "Согласие подписано", "Consent signed"),
            _text("contract_number", "Номер договора", "Contract number", 50),
            _date("contract_signed_on", "Дата договора", "Contract date"),
            Column("passport_number", "Номер паспорта", "Passport number", V.DOCUMENT_NUMBER),
            Column("personal_number", "Личный номер", "Personal number", V.DOCUMENT_NUMBER),
            Column("nationality", "Гражданство", "Nationality", V.COUNTRY),
            _choice("sex", "Пол", "Sex", SEX),
            _date("passport_issued_on", "Паспорт выдан", "Passport issued"),
            _date("passport_expires_on", "Паспорт действует до", "Passport valid until"),
            _text("passport_issued_by", "Кем выдан", "Issued by", 200),
            _text("birth_place", "Место рождения", "Place of birth", 200),
        ),
        "clients",
    ),
    Sheet(
        "notes",
        "Заметки",
        "Notes",
        (
            _code(),
            _client(),
            CLIENT_NAME,
            _date("written_on", "Дата", "Date"),
            _text("body", "Текст", "Text", 5000, required=True),
        ),
        "clients",
    ),
    Sheet(
        "consultations",
        "Консультации",
        "Consultations",
        (
            _code("Код консультации", "Consultation code"),
            _client(),
            CLIENT_NAME,
            _date("held_on", "Дата", "Date", required=True),
            Column("applicants", "Заявителей", "Applicants", V.INT),
            Column("destinations", "Страны", "Countries", V.COUNTRIES),
            _choice("decision", "Решение", "Decision", DECISION),
            Column("channel", "Канал прихода", "Lead channel", V.CHANNEL),
            _date("trip_planned_on", "Планируемая поездка", "Planned trip"),
            _date("follow_up_on", "Вернуться к клиенту", "Follow up on"),
            Column("fee_amount", "Стоимость", "Price", V.MONEY),
            Column("fee_currency", "Валюта", "Currency", V.CURRENCY),
            Column("fee_paid", "Оплачено", "Paid", V.BOOL),
            _date("fee_paid_on", "Дата оплаты", "Paid on"),
            _text("notes", "Заметки", "Notes", 5000),
        ),
        "clients",
    ),
    Sheet(
        "cases",
        "Кейсы",
        "Cases",
        (
            _code("Код кейса", "Case code", required=True),
            _client(),
            CLIENT_NAME,
            Column("number", "Номер кейса", "Case number", V.INT, R),
            Column("family", "Семья", "Family", V.CODE, max_length=CODE_LENGTH),
            Column(
                "consultation",
                "Код консультации",
                "Consultation code",
                V.CODE,
                K,
                max_length=CODE_LENGTH,
                points_to="consultations",
            ),
            _text("source", "Источник", "Source", 100),
            Column("destination", "Страна визы", "Visa country", V.COUNTRY, required=True),
            Column("submission_country", "Страна подачи", "Country of application", V.COUNTRY),
            Column("purpose", "Цель", "Purpose", V.PURPOSE),
            Column("city", "Город подачи", "Application city", V.CITY),
            _choice("hall", "Зал", "Hall", HALL),
            Column("registrar", "Регистратор", "Registrar", V.REGISTRAR),
            _text("vac_number", "Номер в ВЦ", "Visa center number", 64),
            Column("application_email", "Почта заявки", "Application email", V.EMAIL),
            _choice("application_email_owner", "Чья почта", "Whose email", EMAIL_OWNER),
            _choice("stage", "Этап", "Stage", STAGE),
            _date("opened_on", "Дата открытия", "Opened on"),
            _date("questionnaire_due_on", "Анкета до", "Questionnaire due"),
            _date("questionnaire_received_on", "Анкета получена", "Questionnaire received"),
            _date("submission_on", "Дата подачи", "Submitted on"),
            _choice("outcome", "Результат", "Outcome", OUTCOME),
            _choice("visa_entries", "Кратность", "Entries", ENTRIES),
            Column("after_appeal", "После апелляции", "After appeal", V.BOOL),
            _date("decided_on", "Дата решения", "Decided on"),
            _date("visa_valid_from", "Виза с", "Visa valid from"),
            _date("visa_valid_to", "Виза до", "Visa valid to"),
            Column("stay_days", "Дней пребывания", "Days of stay", V.INT),
            Column("price", "Стоимость", "Price", V.MONEY, module="finances"),
            Column("prepayment", "Предоплата", "Prepayment", V.MONEY, module="finances"),
            Column(
                "consultation_credit",
                "Зачёт консультации",
                "Consultation credit",
                V.MONEY,
                module="finances",
            ),
            Column("registrar_fee", "Талон", "Talon", V.MONEY, module="finances"),
            Column(
                "registrar_fee_currency",
                "Валюта талона",
                "Talon currency",
                V.CURRENCY,
                module="finances",
            ),
            Column("registrar_fee_rate", "Курс талона", "Talon rate", V.RATE, module="finances"),
            _date("registrar_fee_on", "Дата талона", "Talon date", module="finances"),
            Column(
                "registrar_fee_settled",
                "Талон оплачен до CRM",
                "Talon paid before CRM",
                V.BOOL,
                module="finances",
            ),
            _choice(
                "partner_terms",
                "Условия партнёра",
                "Partner terms",
                PARTNER_TERMS,
                module="finances",
            ),
            Column(
                "partner_percent",
                "Процент партнёру",
                "Partner percent",
                V.PERCENT,
                module="finances",
            ),
            Column(
                "partner_fixed", "Выплата партнёру", "Partner payout", V.MONEY, module="finances"
            ),
            _date("partner_paid_on", "Выплачено партнёру", "Partner paid on", module="finances"),
            Column("paid", "Оплачено", "Paid", V.MONEY, R, module="finances"),
            Column("balance", "Остаток", "Balance", V.MONEY, R, module="finances"),
        ),
        "cases",
    ),
    Sheet(
        "payments",
        "Платежи",
        "Payments",
        (
            _code(),
            _case(),
            CLIENT_NAME,
            _date("paid_on", "Дата", "Date", required=True),
            Column("amount", "Сумма", "Amount", V.MONEY, required=True),
            Column("currency", "Валюта", "Currency", V.CURRENCY),
            Column("rate", "Курс", "Rate", V.RATE),
            Column("official", "Официальный", "Official", V.BOOL),
            _text("note", "Комментарий", "Comment", 200),
        ),
        "finances",
    ),
    Sheet(
        "expenses",
        "Расходы",
        "Expenses",
        (
            _code(),
            _case(),
            CLIENT_NAME,
            _date("spent_on", "Дата", "Date", required=True),
            _text("description", "Описание", "Description", 200, required=True),
            Column("amount", "Сумма", "Amount", V.MONEY, required=True),
            Column("currency", "Валюта", "Currency", V.CURRENCY),
            Column("rate", "Курс", "Rate", V.RATE),
        ),
        "finances",
    ),
    Sheet(
        "queue",
        "Очередь",
        "Queue",
        (
            _code(),
            _case(),
            CLIENT_NAME,
            COUNTRY_REF,
            _date("queued_on", "Дата постановки", "Queued on", required=True),
            _date("letter_on", "Дата письма", "Letter on"),
            _date("removed_on", "Дата снятия", "Removed on"),
            _text("removed_reason", "Причина снятия", "Removal reason", 500),
        ),
        "cases",
    ),
    Sheet(
        "attempts",
        "Заявки с окном",
        "Windowed applications",
        (
            _code(),
            _case(),
            CLIENT_NAME,
            COUNTRY_REF,
            Column("number", "Номер попытки", "Attempt number", V.INT),
            _date("sent_on", "Дата отправки", "Sent on", required=True),
            _date("deadline_on", "Последний день окна", "Last day of the window"),
            _choice("outcome", "Итог", "Outcome", ATTEMPT_OUTCOME),
            _date("closed_on", "Дата закрытия", "Closed on"),
        ),
        "cases",
    ),
    Sheet(
        "bookings",
        "Брони",
        "Bookings",
        (
            _code(),
            _case(),
            CLIENT_NAME,
            _choice("kind", "Вид", "Kind", BOOKING_KIND),
            _choice("status", "Статус", "Status", BOOKING_STATUS),
            _text("place", "Место", "Place", 200, required=True),
            _date("starts_on", "Заезд", "Check-in"),
            _date("ends_on", "Выезд", "Check-out"),
            _date("free_cancel_until", "Бесплатная отмена до", "Free cancellation until"),
            _date("valid_until", "Действует до", "Valid until"),
            _text("reference", "Номер брони", "Booking number", 64),
            _text("account", "Аккаунт", "Account", 320, module="vault"),
            _text("note", "Комментарий", "Comment", 500),
        ),
        "cases",
    ),
    Sheet(
        "accounts",
        "Учётные записи",
        "Accounts",
        (
            _code(),
            _client(required=False),
            _case(required=False),
            CLIENT_NAME,
            Column("system", "Система", "System", V.SYSTEM, required=True),
            _text("login", "Логин", "Login", 320, required=True),
            Column("password", "Пароль", "Password", V.SECRET, max_length=200),
            _choice("email_owner", "Чья почта", "Whose email", EMAIL_OWNER),
            Column("created_manually", "Создала сама", "Created by me", V.BOOL),
            _text("extra_id", "Доп. идентификатор", "Extra ID", 100),
            _text("note", "Заметка", "Note", 1000),
        ),
        "vault",
    ),
)
BY_KEY = {sheet.key: sheet for sheet in SHEETS}

# Sheets of the export and the template only; the import passes them by.
REFERENCE_SHEET = ("Справочник", "Reference")
ABOUT_SHEET = ("О файле", "About")
# The rows of «О файле» the import reads: when the file was exported.
ABOUT_EXPORTED = ("Выгружено", "Exported")
