"""Cards before the import (backlog 1.22, docs/data-format.md → «Карточки до загрузки»): the
sample is the file's first clients with everything of theirs, and a card shows the format's
columns in the interface's language, a password only as «есть»."""

from datetime import date
from decimal import Decimal

from app.core.data_format import BY_KEY
from app.core.import_cards import Card, Localized, Record, render, summary
from app.core.workbook import Issue, Row, SheetData, Workbook, first_clients


def sheet(key: str, *rows: dict) -> SheetData:
    return SheetData(
        BY_KEY[key], BY_KEY[key].ru, {}, [Row(n, values) for n, values in enumerate(rows, 2)]
    )


def codes(book: Workbook, key: str, column: str = "code") -> list:
    return [row.values.get(column) for row in book.sheets[key].rows] if key in book.sheets else []


def test_a_sample_is_the_first_clients_with_their_rows_on_every_sheet():
    book = Workbook(
        sheets={
            "clients": sheet("clients", {"code": "K1"}, {"code": "K2"}, {"code": "K3"}),
            "notes": sheet("notes", {"client": "K3", "body": "a"}, {"client": "K1", "body": "b"}),
            "consultations": sheet("consultations", {"code": "S2", "client": "K2"}),
            "cases": sheet(
                "cases",
                {"code": "C1", "client": "K1"},
                {"code": "C3", "client": "K3"},
                {"code": "C2", "client": "K2"},
            ),
            "payments": sheet("payments", {"case": "C3"}, {"case": "C1"}, {"case": "C1"}),
            "accounts": sheet(
                "accounts",
                {"code": "A1", "client": "K1"},
                {"code": "A2", "case": "C2"},
                {"code": "A3"},  # the specialist's own: no client of the sample
                {"code": "A4", "client": "K3"},
            ),
        },
        exported_at=None,
        skipped=[Issue("no_name", "Клиенты", 9)],
        warnings=[Issue("unknown_sheet", "Почты")],
    )

    part = first_clients(book, 2)

    assert codes(part, "clients") == ["K1", "K2"]
    assert codes(part, "notes", "client") == ["K1"]
    assert codes(part, "consultations") == ["S2"]
    assert codes(part, "cases") == ["C1", "C2"]
    assert codes(part, "payments", "case") == ["C1", "C1"]
    assert codes(part, "accounts") == ["A1", "A2"]
    assert (part.skipped, part.warnings) == ([], [])
    # The sample keeps the file's sheets as they were: the whole file is read again.
    assert len(book.sheets["clients"].rows) == 3


def test_a_sample_without_the_clients_sheet_takes_the_clients_other_sheets_name():
    book = Workbook(
        sheets={
            "consultations": sheet(
                "consultations", {"client": "K5"}, {"client": "K6"}, {"client": "K5"}
            ),
            "cases": sheet("cases", {"code": "C7", "client": "K7"}, {"code": "C5", "client": "K5"}),
        }
    )

    part = first_clients(book, 2)

    assert codes(part, "consultations", "client") == ["K5", "K6", "K5"]
    assert codes(part, "cases") == ["C5"]


CARD = Card(
    status="new",
    name="Петрова Анна",
    phone="+375291112233",
    records=(
        Record(
            "clients",
            "new",
            {
                "last_name": "Петрова",
                "first_name": "Анна",
                "middle_name": None,
                "phone": "+375291112233",
                "birth_date": date(1990, 5, 1),
                "sex": "F",
                "nationality": Localized("Беларусь", "Belarus"),
            },
        ),
        Record(
            "cases",
            "new",
            {
                "number": 7,
                "destination": Localized("Польша", "Poland"),
                "stage": "documents",
                "price": Decimal("800.00"),
                "registrar_fee_settled": True,
            },
            children=(
                Record(
                    "payments",
                    "new",
                    {"paid_on": date(2025, 3, 1), "amount": Decimal("100.00"), "currency": "EUR"},
                ),
                Record("accounts", None, {"system": "Почта", "login": "a@x.by", "password": True}),
            ),
        ),
    ),
    issues=({"sheet": "Платежи", "row": 3, "column": "Курс", "reason": "rate_unavailable"},),
)


def fields(record: dict) -> dict:
    return {f["label"]: (f["type"], f["value"]) for f in record["fields"]}


def test_a_card_shows_the_formats_columns_in_the_interface_language():
    ru, en = render(CARD, "ru"), render(CARD, "en")

    assert [s["title"] for s in ru["sections"]] == ["Клиенты", "Кейсы"]
    assert [s["title"] for s in en["sections"]] == ["Clients", "Cases"]
    [client] = ru["sections"][0]["records"]
    assert client["change"] == "new"
    # The format's order, its words for a fixed list, dates as dates; an empty field is left out.
    assert fields(client) == {
        "Фамилия": ("text", "Петрова"),
        "Имя": ("text", "Анна"),
        "Телефон": ("text", "+375291112233"),
        "Дата рождения": ("date", "1990-05-01"),
        "Гражданство": ("text", "Беларусь"),
        "Пол": ("text", "Ж"),
    }
    assert fields(en["sections"][0]["records"][0])["Sex"] == ("text", "F")
    assert fields(en["sections"][0]["records"][0])["Nationality"] == ("text", "Belarus")

    [case] = ru["sections"][1]["records"]
    assert fields(case) == {
        "Номер кейса": ("number", "7"),
        "Страна визы": ("text", "Польша"),
        "Этап": ("text", "сбор документов"),
        "Стоимость": ("money", "800.00"),
        "Талон оплачен до CRM": ("bool", True),
    }
    payments, accounts = case["sections"]
    assert (payments["title"], accounts["title"]) == ("Платежи", "Учётные записи")
    assert fields(payments["records"][0]) == {
        "Дата": ("date", "2025-03-01"),
        "Сумма": ("money", "100.00"),
        "Валюта": ("text", "EUR"),
    }
    # A password is never shown: only that there is one.
    assert accounts["records"][0]["change"] is None
    assert fields(accounts["records"][0])["Пароль"] == ("secret", True)
    assert ru["issues"] == list(CARD.issues)
    assert (ru["status"], ru["name"], ru["phone"]) == ("new", "Петрова Анна", "+375291112233")


def test_the_list_of_cards_counts_cases_consultations_and_issues():
    assert summary(CARD, 4) == {
        "n": 4,
        "status": "new",
        "name": "Петрова Анна",
        "phone": "+375291112233",
        "consultations": 0,
        "cases": 1,
        "issues": 1,
    }
