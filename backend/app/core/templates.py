"""Document templates (backlog 2.1): the codes a specialist types into her own Word files,
what each one inserts and how the filled file is named. The filling itself is
app/core/docx_fill.py; the instruction with every code is on the «Шаблоны документов»
page (GET /api/templates/codes).

A code is written in Russian or in English, in any case, with spaces or underscores:
`{{Дата рождения}}`, `{{date_of_birth}}`. The language it is written in is the language of
the value where there is a choice (a country's name, sex, how a sum is written), so an
English letter gets «Belarus» and a Russian one «Беларусь».
"""

import re
import unicodedata
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Literal

from app.core.crypto import decrypt, encrypt
from app.core.docx_fill import CODE

Lang = Literal["ru", "en"]


@dataclass(frozen=True)
class Code:
    key: str
    # How the instruction groups them: client, passport, case, money, other.
    group: str
    # The ways to write it; the first one is shown in the instruction.
    ru: tuple[str, ...]
    en: tuple[str, ...]


CODES: tuple[Code, ...] = (
    Code("full_name", "client", ("ФИО",), ("Full name",)),
    Code("last_name", "client", ("Фамилия",), ("Surname", "Last name")),
    Code("first_name", "client", ("Имя",), ("First name", "Given name")),
    Code("middle_name", "client", ("Отчество",), ("Patronymic", "Middle name")),
    Code("full_name_latin", "client", ("ФИО латиницей",), ("Full name latin",)),
    Code("last_name_latin", "client", ("Фамилия латиницей",), ("Surname latin", "Last name latin")),
    Code(
        "first_name_latin", "client", ("Имя латиницей",), ("Given names latin", "First name latin")
    ),
    Code("birth_date", "client", ("Дата рождения",), ("Date of birth", "Birth date")),
    Code("phone", "client", ("Телефон",), ("Phone",)),
    Code("email", "client", ("Почта", "Email", "E-mail"), ("Email", "E-mail")),
    Code("telegram", "client", ("Телеграм",), ("Telegram",)),
    Code("contract_number", "client", ("Номер договора",), ("Contract number",)),
    Code("contract_date", "client", ("Дата договора",), ("Contract date",)),
    Code("passport_number", "passport", ("Номер паспорта",), ("Passport number",)),
    Code("passport_issued_on", "passport", ("Дата выдачи паспорта",), ("Passport issue date",)),
    Code(
        "passport_expires_on", "passport", ("Паспорт действителен до",), ("Passport expiry date",)
    ),
    Code(
        "passport_issued_by",
        "passport",
        ("Кем выдан паспорт", "Кем выдан"),
        ("Passport issued by", "Issuing authority"),
    ),
    Code("nationality", "passport", ("Гражданство",), ("Nationality",)),
    Code("sex", "passport", ("Пол",), ("Sex",)),
    Code("personal_number", "passport", ("Личный номер",), ("Personal number",)),
    Code("birth_place", "passport", ("Место рождения",), ("Place of birth", "Birth place")),
    Code("case_number", "case", ("Номер кейса",), ("Case number",)),
    Code("destination", "case", ("Страна визы",), ("Visa country", "Destination")),
    Code("submission_country", "case", ("Страна подачи",), ("Submission country",)),
    Code("purpose", "case", ("Цель поездки", "Цель"), ("Purpose",)),
    Code("submission_date", "case", ("Дата подачи",), ("Submission date",)),
    Code("vac_number", "case", ("Номер в визовом центре",), ("Visa center number",)),
    Code("price", "money", ("Стоимость",), ("Price",)),
    Code("prepayment", "money", ("Предоплата",), ("Prepayment",)),
    Code("today", "other", ("Сегодня",), ("Today",)),
)

# The longest a file name may be, without the extension.
MAX_NAME = 150
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


@dataclass(frozen=True)
class Localized:
    """A value that reads differently in a Russian and an English document."""

    ru: str
    en: str


@dataclass(frozen=True)
class Money:
    amount: Decimal
    currency: str


SEX = {
    "M": Localized("мужской", "male"),
    "F": Localized("женский", "female"),
    "X": Localized("не указан", "unspecified"),
}


def normalize(code: str) -> str:
    """One spelling of a code: any case, «ё» as «е», spaces and underscores alike."""
    text = unicodedata.normalize("NFKC", code).casefold().replace("ё", "е")
    return re.sub(r"[\s_]+", " ", text).strip()


def _spellings() -> dict[str, tuple[Code, Lang]]:
    found: dict[str, tuple[Code, Lang]] = {}
    for code in CODES:
        for spelling in code.en:
            found[normalize(spelling)] = (code, "en")
        # A spelling both languages share (Email) reads as Russian: its value is the same.
        for spelling in code.ru:
            found[normalize(spelling)] = (code, "ru")
    return found


_BY_SPELLING = _spellings()


def resolve(code: str) -> tuple[Code, Lang] | None:
    """The code and the language it is written in; None for one that does not exist."""
    return _BY_SPELLING.get(normalize(code))


def split_codes(written: list[str]) -> tuple[list[str], list[str]]:
    """Codes as found in a file → the known keys and the unknown codes as written, each
    once and in the order they come."""
    known: dict[str, None] = {}
    unknown: dict[str, str] = {}
    for code in written:
        found = resolve(code)
        if found is not None:
            known.setdefault(found[0].key, None)
        elif normalize(code):
            unknown.setdefault(normalize(code), code.strip())
    return list(known), list(unknown.values())


def format_value(value: object, lang: Lang) -> str:
    """How a value is written in a document: dates as 05.10.1990, sums with the currency
    in the way of the document's language."""
    if value is None:
        return ""
    if isinstance(value, Localized):
        return value.ru if lang == "ru" else value.en
    if isinstance(value, Money):
        text = f"{value.amount:,.2f}"
        if lang == "ru":
            text = text.replace(",", " ").replace(".", ",")
        return f"{text} {value.currency}"
    if isinstance(value, date):
        return value.strftime("%d.%m.%Y")
    return str(value).strip()


def filler(data: Mapping[str, object]) -> Callable[[str], str | None]:
    """For docx_fill.fill: the text of a code from the client's data (empty when it is not
    filled in), None for an unknown code."""

    def value_of(code: str) -> str | None:
        found = resolve(code)
        if found is None:
            return None
        known, lang = found
        return format_value(data.get(known.key), lang)

    return value_of


def missing(keys: list[str], data: Mapping[str, object]) -> list[str]:
    """The codes of a template the client has no value for yet."""
    return [key for key in keys if not format_value(data.get(key), "ru")]


# Characters Windows and macOS do not allow in file names, and control characters.
_NOT_IN_NAMES = re.compile(r'[\\/:*?"<>|\x00-\x1f\x7f]')


def file_name(pattern: str | None, template_name: str, data: Mapping[str, object]) -> str:
    """The name of the filled file: by the template's pattern (text with codes), or
    «<template> — <client>». Unknown codes and characters a file name cannot have are
    dropped."""
    if pattern is None:
        client = format_value(data.get("full_name"), "ru")
        stem = f"{template_name} — {client}" if client else template_name
    else:
        value_of = filler(data)
        stem = CODE.sub(lambda match: value_of(match.group(1)) or "", pattern)
    stem = " ".join(_NOT_IN_NAMES.sub(" ", unicodedata.normalize("NFC", stem)).split())
    stem = stem[:MAX_NAME].strip(" .") or "document"
    return f"{stem}.docx"


def clean_source_name(name: str) -> str:
    """The uploaded file's name, to download the blank template with: no folders or
    characters a file name cannot have, and the .docx extension."""
    name = unicodedata.normalize("NFC", name).replace("\\", "/").rsplit("/", 1)[-1]
    stem = name[:-5] if name.lower().endswith(".docx") else name
    stem = " ".join(_NOT_IN_NAMES.sub(" ", stem).split())[:MAX_NAME].strip(" .")
    return f"{stem or 'template'}.docx"


def object_key(tenant_id: uuid.UUID, template_id: uuid.UUID) -> str:
    """Ids only, like client files (app/core/documents.py)."""
    return f"templates/{tenant_id}/{template_id}"


def _aad(tenant_id: uuid.UUID, template_id: uuid.UUID) -> bytes:
    return b"template:" + tenant_id.bytes + template_id.bytes


def seal_template(key: bytes, tenant_id: uuid.UUID, template_id: uuid.UUID, data: bytes) -> bytes:
    return encrypt(key, data, _aad(tenant_id, template_id))


def open_template(key: bytes, tenant_id: uuid.UUID, template_id: uuid.UUID, blob: bytes) -> bytes:
    """Raises InvalidTag if the blob is not this template's or was changed in the storage."""
    return decrypt(key, blob, _aad(tenant_id, template_id))
