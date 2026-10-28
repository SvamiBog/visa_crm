"""The made-up pilot book (tests/pilot_book.py) turned into the VisaCRM data format by
following the universal prompt (docs/import-prompt.md) in mode 2 — passwords included —,
the way an AI would after asking the specialist. Her answers assumed here:

- «Оплата моя посредникам» is the talon only (question 20); «Пароли» of France — the email's
  password, then the TLScontact site's, both on the same login;
- «Список ЖО25» is Italy; «Латвия с 17.08» is of 2026; Italy's new visa center is VFS;
- «Наталья», «Наталья В.» and «Наташа» are one registrar; every «Откуда» is a channel;
- «Акция», «Дата отправки заявки» of the Italian lists and «Данные для заявки» are not
  carried over; the application email of Anna's case of 2026 is the one she made herself.

Guessed values are marked in the comments, as the AI lists them in its report. Skipped:
the zero rows and totals of every sheet, «согласие: да» of 2023 (no date), the unnamed
husband and son of «Орлова Нина + супруг + сын» (a note), «Стоимость в злотых».

Also written to an .xlsx for a rehearsal of the import on a test stand:
    uv run python -m tests.pilot_book_visacrm VisaCRM-импорт.xlsx
"""

# One row of a sheet per line: the file reads as the workbook does.
# ruff: noqa: E501

import sys
from datetime import date

d = date

CLIENTS = [
    ["Код клиента", "Фамилия", "Имя", "Телефон", "Telegram", "Email", "Дата рождения", "Канал прихода"],
    ["Подача26-2", "Иванова", "Анна", "+375291111111", "@anna_iv", "anna@mail.ru", d(1990, 5, 1), "Сарафанка"],
    ["Подача26-3", "Петров", "Пётр", "+375291112233", None, None, None, "PK doc"],
    ["Подача26-4", "Морозов", "Денис", "+375334444444", None, None, None, "Друзья"],
    ["Подача26-5", "Кузнецова", "Вера", "+375445555555", None, None, None, "рефералка"],
    ["Подача26-6", "Сидоров", "Олег", None, None, None, None, "Сарафанка"],
    ["Подача26-7", "Орлова", "Нина", "+375296666666", None, None, None, "Сарафанка"],
    ["Консультации26-7", "Сидорова", "Ирина", None, None, None, None, None],
    ["Латвия с 17.08-2", "Новикова", "Ольга", None, None, None, None, None],
    ["Подача25-3", "Зайцева", "Алла", "+375257777777", None, None, None, "Друзья"],
    ["Консультации25-3", "Козлова", "Мария", "+375293333333", "@maria_k", None, d(1995, 3, 3), "Инста"],
]  # fmt: skip

NOTES = [
    ["Код клиента", "Клиент", "Текст"],
    ["Подача26-2", "Иванова Анна", "от Ольги Т."],
    ["Подача26-3", "Петров Пётр", "от Ольги Т."],
    ["Подача26-6", "Сидоров Олег", "связь в вайбере"],
    ["Подача26-6", "Сидоров Олег", "верификация 12.06"],
    ["Консультации26-7", "Сидорова Ирина", "связь в вайбере"],
    ["Подача26-7", "Орлова Нина", "подаётся с супругом и сыном"],
    ["Подача25-3", "Зайцева Алла", "подались"],
]

CONSULTATIONS = [
    ["Код консультации", "Код клиента", "Клиент", "Дата", "Заявителей", "Страны", "Решение", "Стоимость", "Оплачено", "Заметки"],
    ["Консультации26-2", "Подача26-2", "Иванова Анна", d(2026, 1, 15), 1, "Польша", "да", 50, "да", "Польша, туризм"],
    ["Консультации26-3", "Подача26-3", "Петров Пётр", d(2026, 2, 20), 1, "Греция", "да", None, None, None],
    ["Консультации26-4", "Подача26-4", "Морозов Денис", d(2026, 4, 1), 1, "Франция", "да", None, None, None],
    ["Консультации26-5", "Подача26-5", "Кузнецова Вера", d(2026, 1, 5), 1, "Италия", "да", None, None, None],
    ["Консультации26-6", "Подача26-6", "Сидоров Олег", d(2026, 6, 1), 2, None, "думает", None, None, None],
    ["Консультации26-7", "Консультации26-7", "Сидорова Ирина", d(2026, 9, 2), 1, None, "нет", None, None, None],
    ["Консультации25-2", "Подача26-2", "Иванова Анна", d(2025, 3, 10), 1, "Польша", "да", None, None, None],
    # «06.04.0205»: the 1st of «Апрель» of the sheet's year (marked).
    ["Консультации25-3", "Консультации25-3", "Козлова Мария", d(2025, 4, 1), 1, None, "нет", None, None, None],
    # No date: the 1st of «Март» of 2024 (marked).
    ["2024 Консультации-2", "Консультации25-3", "Козлова Мария", d(2024, 3, 1), 1, "Испания", "да", 40, "да", "Испания, учёба"],
]  # fmt: skip

CASES = [
    ["Код кейса", "Код клиента", "Клиент", "Код консультации", "Источник", "Страна визы", "Цель", "Город подачи", "Регистратор", "Номер в ВЦ", "Почта заявки", "Чья почта", "Дата открытия", "Дата подачи", "Результат", "Кратность", "Стоимость", "Талон", "Талон оплачен до CRM", "Условия партнёра", "Выплата партнёру", "Выплачено партнёру"],
    ["Подача26-2", "Подача26-2", "Иванова Анна", "Консультации26-2", "Подача26", "Польша", "Туризм", "Минск", "Наталья", "PLB1234567890", "anna.visa@gmail.com", "создала я", None, d(2026, 6, 8), None, None, 400, 120, "да", None, None, None],
    # «Мультик»: a multiple visa; paid out to the partner on the submission day (marked).
    ["Подача26-3", "Подача26-3", "Петров Пётр", "Консультации26-3", "Подача26", "Греция", "Туризм", None, None, "AB12", None, None, None, d(2026, 5, 20), "виза", "многократная", 300, None, None, "фикс", 30, d(2026, 5, 20)],
    ["Подача26-4", "Подача26-4", "Морозов Денис", "Консультации26-4", "Подача26", "Франция", "Туризм", None, None, None, "denis.fr@gmail.com", "создала я", None, None, None, None, 350, None, None, None, None, None],
    ["Подача26-5", "Подача26-5", "Кузнецова Вера", "Консультации26-5", "Подача26", "Италия", "Гостевая", "Минск", "Агентство Виза", "ITA123456789012", "vera@mail.ru", "клиента", None, d(2026, 7, 20), None, None, 450, 150, "да", None, None, None],
    ["Подача26-6", "Подача26-6", "Сидоров Олег", "Консультации26-6", "Подача26", "Польша", "Туризм", "Гродно", "Наталья", None, None, None, None, None, None, None, 400, 120, "да", None, None, None],
    ["Подача26-7", "Подача26-7", "Орлова Нина", None, "Подача26", "Польша", "Туризм", None, "Наталья", None, None, None, None, None, None, None, 900, 360, "да", None, None, None],
    # «Однократка»: a single visa.
    ["Подача25-2", "Подача26-2", "Иванова Анна", "Консультации25-2", "Подача25", "Польша", "Туризм", None, "Наталья", None, None, None, None, d(2025, 7, 1), "виза", "однократная", 350, 100, "да", None, None, None],
    ["Подача25-3", "Подача25-3", "Зайцева Алла", None, "Подача25", "Италия", "Туризм", None, "Агентство Виза", "12345", None, None, None, d(2025, 9, 15), "виза", "многократная", 500, 150, "да", None, None, None],
    ["2024 Подача-2", "Консультации25-3", "Козлова Мария", "2024 Консультации-2", "2024 Подача", "Испания", "студент", None, None, None, None, None, None, d(2024, 6, 1), "отказ", None, 300, None, None, None, None, None],
    # Not a single date: opened on the 1st of January of the sheet's year (marked).
    ["2023-2", "Подача26-3", "Петров Пётр", None, "2023", "Греция", "Туризм", None, None, "CD34", None, None, d(2023, 1, 1), None, None, None, None, None, None, None, None, None],
    ["Латвия с 17.08-2", "Латвия с 17.08-2", "Новикова Ольга", None, "Латвия с 17.08", "Латвия", None, "Витебск", None, "LVA123456789012", None, None, None, None, None, None, 350, None, None, None, None, None],
]  # fmt: skip

# What was paid: «Стоимость» minus «Остаток»; without a day of payment — on the
# submission day, else on the queue or application day (marked).
PAYMENTS = [
    ["Код кейса", "Клиент", "Дата", "Сумма"],
    ["Подача26-2", "Иванова Анна", d(2026, 6, 8), 200],
    ["Подача26-3", "Петров Пётр", d(2026, 5, 20), 300],
    ["Подача26-4", "Морозов Денис", d(2026, 5, 1), 100],
    ["Подача26-5", "Кузнецова Вера", d(2026, 7, 20), 450],
    ["Подача26-7", "Орлова Нина", d(2026, 6, 12), 300],
    ["Подача25-2", "Иванова Анна", d(2025, 7, 1), 350],
    ["Подача25-3", "Зайцева Алла", d(2025, 9, 15), 500],
    ["2024 Подача-2", "Козлова Мария", d(2024, 6, 1), 300],
    ["Латвия с 17.08-2", "Новикова Ольга", d(2026, 8, 17), 350],
]

QUEUE = [
    ["Код кейса", "Клиент", "Страна", "Дата постановки", "Дата письма", "Дата снятия", "Причина снятия"],
    ["Подача26-2", "Иванова Анна", "Польша", d(2026, 3, 1), d(2026, 5, 20), None, None],
    ["Подача26-6", "Сидоров Олег", "Польша", d(2026, 6, 10), None, None, None],
    ["Подача26-7", "Орлова Нина", "Польша", d(2026, 6, 12), None, None, None],
    # «17.08 в 15.57»: the year the specialist named (marked).
    ["Латвия с 17.08-2", "Новикова Ольга", "Латвия", d(2026, 8, 17), None, None, None],
    # «август 2025»: the 1st of the month (marked); moved to the new visa center.
    ["Подача26-5", "Кузнецова Вера", "Италия", d(2025, 8, 1), None, d(2026, 5, 5), "перевод в новый ВЦ"],
    ["Подача26-5", "Кузнецова Вера", "Италия", d(2026, 5, 5), d(2026, 7, 1), None, None],
    # «август 2024»: the 1st of the month (marked).
    ["Подача25-3", "Зайцева Алла", "Италия", d(2024, 8, 1), None, None, None],
]  # fmt: skip

ATTEMPTS = [
    ["Код кейса", "Клиент", "Страна", "Номер попытки", "Дата отправки", "Последний день окна", "Итог", "Дата закрытия"],
    ["Подача26-4", "Морозов Денис", "Франция", 1, d(2026, 5, 1), d(2026, 6, 29), "истекла", d(2026, 6, 29)],
    ["Подача26-4", "Морозов Денис", "Франция", 2, d(2026, 7, 1), d(2026, 8, 29), "отменена", None],
]  # fmt: skip

ACCOUNTS = [
    ["Код клиента", "Код кейса", "Клиент", "Система", "Логин", "Пароль", "Чья почта", "Создала сама", "Заметка"],
    ["Подача26-2", "Подача26-2", "Иванова Анна", "Почта", "anna.visa@gmail.com", "Pa55word", "создала я", "да", None],
    ["Подача26-2", "Подача26-2", "Иванова Анна", "Почта", "anna.pl@mail.ru", "PlPass1", "клиента", None, None],
    ["Подача26-2", "Подача26-2", "Иванова Анна", "Почта", "fwd@mail.ru", "FwdPass1", "клиента", None, "https://mail.example/fwd"],
    ["Подача26-4", "Подача26-4", "Морозов Денис", "Почта", "denis.fr@gmail.com", "p1", "создала я", "да", None],
    ["Подача26-4", "Подача26-4", "Морозов Денис", "TLScontact", "denis.fr@gmail.com", "p2", None, None, None],
    ["Подача26-5", "Подача26-5", "Кузнецова Вера", "VFS Global", "ITA123456789012", "CabPass1", None, None, None],
    [None, None, None, "Booking.com", "booking.visa@gmail.com", "BkPass1", "моя резервная", None, None],
    [None, None, None, "Почта", "old.visa@gmail.com", "OldPass1", "моя резервная", None, "удалила"],
]  # fmt: skip

# No hotel names in the sheet: «Booking» (listed in the report).
BOOKINGS = [
    ["Код кейса", "Клиент", "Место", "Бесплатная отмена до", "Аккаунт", "Комментарий"],
    ["Подача26-2", "Иванова Анна", "Booking", d(2026, 12, 25), "booking.visa@gmail.com", "гостиниц: 2; бронь и на Сидорова Олега"],
    ["Подача26-4", "Морозов Денис", "Booking", d(2026, 12, 17), None, "гостиниц: 1"],
]  # fmt: skip

BOOK = {
    "Клиенты": CLIENTS,
    "Заметки": NOTES,
    "Консультации": CONSULTATIONS,
    "Кейсы": CASES,
    "Платежи": PAYMENTS,
    "Очередь": QUEUE,
    "Заявки с окном": ATTEMPTS,
    "Учётные записи": ACCOUNTS,
    "Брони": BOOKINGS,
}
# The vault systems the pilot workspace has.
SYSTEMS = (
    ("Почта", "email"),
    ("VFS Global", "visa_center"),
    ("TLScontact", "visa_center"),
    ("Booking.com", "booking"),
)


def xlsx() -> bytes:
    import io

    import openpyxl

    workbook = openpyxl.Workbook()
    workbook.remove(workbook.active)
    for name, rows in BOOK.items():
        sheet = workbook.create_sheet(name)
        for row in rows:
            sheet.append(row)
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


if __name__ == "__main__":
    with open(sys.argv[1], "wb") as out:
        out.write(xlsx())
