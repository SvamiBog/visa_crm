"""seed countries, visa center operators and the rules for submissions in Belarus

Source: docs/PRD.md, «Операторы визовых центров» (catalogues of the operators and embassy
sites, 2026-10-05) and the specialist's answers in docs/clients-module.md. Pairs the PRD
marks «?» are seeded as unverified. Direct-slot booking for countries without a waiting
list is an assumption to confirm, so those rules are unverified too. Cities (visa centers)
are not seeded: the admin adds them.

Revision ID: 0010
Revises: 0009
Create Date: 2026-10-15

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# code, Russian name, English name, typical consultation minutes (None: not a destination).
COUNTRIES = [
    ("AT", "Австрия", "Austria", 30),
    ("BE", "Бельгия", "Belgium", 30),
    ("BG", "Болгария", "Bulgaria", 30),
    ("GB", "Великобритания", "United Kingdom", 60),
    ("HU", "Венгрия", "Hungary", 30),
    ("DE", "Германия", "Germany", 30),
    ("GR", "Греция", "Greece", 30),
    ("DK", "Дания", "Denmark", 30),
    ("IE", "Ирландия", "Ireland", 30),
    ("ES", "Испания", "Spain", 30),
    ("IT", "Италия", "Italy", 30),
    ("CY", "Кипр", "Cyprus", 30),
    ("LV", "Латвия", "Latvia", 30),
    ("LT", "Литва", "Lithuania", 30),
    ("LU", "Люксембург", "Luxembourg", 30),
    ("MT", "Мальта", "Malta", 30),
    ("NL", "Нидерланды", "Netherlands", 30),
    ("PL", "Польша", "Poland", 30),
    ("PT", "Португалия", "Portugal", 30),
    ("RO", "Румыния", "Romania", 30),
    ("SK", "Словакия", "Slovakia", 30),
    ("SI", "Словения", "Slovenia", 30),
    ("FI", "Финляндия", "Finland", 30),
    ("FR", "Франция", "France", 30),
    ("HR", "Хорватия", "Croatia", 30),
    ("CZ", "Чехия", "Czechia", 30),
    ("SE", "Швеция", "Sweden", 30),
    ("EE", "Эстония", "Estonia", 30),
    ("US", "США", "United States", 60),
    ("CA", "Канада", "Canada", 60),
    ("AU", "Австралия", "Australia", 60),
    ("JP", "Япония", "Japan", 30),
    # Countries where clients submit applications.
    ("BY", "Беларусь", "Belarus", None),
    ("RU", "Россия", "Russia", None),
    ("GE", "Грузия", "Georgia", None),
    ("AM", "Армения", "Armenia", None),
    ("KZ", "Казахстан", "Kazakhstan", None),
    ("AZ", "Азербайджан", "Azerbaijan", None),
    ("TR", "Турция", "Türkiye", None),
    ("RS", "Сербия", "Serbia", None),
]

OPERATORS = [
    ("vfs", "VFS Global"),
    ("tlscontact", "TLScontact"),
    ("bls", "BLS International"),
    ("gvc_world", "GVC World"),
    ("visametric", "VisaMetric"),
    ("vms", "VMS"),
    ("almaviva", "Almaviva"),
    ("capago", "Capago"),
]

WL, WIN, SLOT = "waiting_list", "windowed_application", "direct_slot"

# Submissions in Belarus: destination, booking type, operator (None: the embassy),
# represented by, identifier type, verified, note.
BELARUS_RULES = [
    ("AT", WL, "vfs", None, "application_number", True, None),
    ("BE", WL, None, None, "application_number", False, "Оператор не подтверждён"),
    ("BG", SLOT, "vfs", None, None, False, None),
    ("GB", SLOT, "vfs", None, None, False, None),
    ("HU", SLOT, "vfs", None, None, False, None),
    ("DE", WL, None, None, "application_number", True, "Лист ожидания посольства"),
    ("GR", SLOT, "gvc_world", None, "username", False, None),
    ("DK", SLOT, "vfs", None, None, False, None),
    ("IE", SLOT, "vfs", None, None, False, None),
    ("ES", SLOT, "bls", None, None, False, None),
    ("IT", WL, "vfs", None, "vac_id", True, "VFS с 01.05.2026, раньше TLScontact"),
    ("CY", SLOT, "bls", None, None, False, None),
    ("LV", WL, "vfs", None, "vac_id", True, None),
    ("LT", SLOT, "vfs", None, None, False, None),
    ("LU", WL, None, None, "application_number", False, "Оператор не подтверждён"),
    ("MT", SLOT, "vfs", None, None, False, "Через VFS — только национальные визы (D)"),
    ("NL", WL, None, "DE", "application_number", True, "Через посольство Германии"),
    ("PL", WL, "vfs", None, "vac_id", True, None),
    ("SK", SLOT, "vfs", None, None, False, None),
    ("SI", WL, None, None, "application_number", False, "Оператор не подтверждён"),
    ("FI", SLOT, "vfs", None, None, False, None),
    ("FR", WIN, "tlscontact", None, "form_number", True, None),
    ("HR", SLOT, "vfs", None, None, False, None),
    ("CZ", SLOT, "vfs", None, None, False, None),
    ("SE", SLOT, "vfs", "HU", None, False, "Через центр Венгрии"),
    ("EE", SLOT, "vfs", None, None, False, None),
]
FRANCE_WINDOW_DAYS = 60


def upgrade() -> None:
    # The migration runs as the database owner, outside the app role: RLS does not apply.
    conn = op.get_bind()
    for code, name_ru, name_en, minutes in COUNTRIES:
        conn.execute(
            sa.text(
                "INSERT INTO countries (id, code, name_ru, name_en, consultation_minutes) "
                "VALUES (gen_random_uuid(), :code, :ru, :en, :minutes)"
            ),
            {"code": code, "ru": name_ru, "en": name_en, "minutes": minutes},
        )
    for code, name in OPERATORS:
        conn.execute(
            sa.text(
                "INSERT INTO vac_operators (id, code, name) VALUES (gen_random_uuid(), :c, :n)"
            ),
            {"c": code, "n": name},
        )
    for dest, booking, operator, represented, identifier, verified, note in BELARUS_RULES:
        conn.execute(
            sa.text(
                "INSERT INTO country_rules (id, destination_id, submission_id, booking_type, "
                "window_days, vac_operator_id, represented_by_id, identifier_type, "
                "is_verified, notes) VALUES (gen_random_uuid(), "
                "(SELECT id FROM countries WHERE code = :dest), "
                "(SELECT id FROM countries WHERE code = 'BY'), "
                "CAST(:booking AS booking_type), :window, "
                "(SELECT id FROM vac_operators WHERE code = :operator), "
                "(SELECT id FROM countries WHERE code = :represented), "
                "CAST(:identifier AS vac_identifier_type), :verified, :note)"
            ),
            {
                "dest": dest,
                "booking": booking,
                "window": FRANCE_WINDOW_DAYS if booking == WIN else None,
                "operator": operator,
                "represented": represented,
                "identifier": identifier,
                "verified": verified,
                "note": note,
            },
        )


def downgrade() -> None:
    op.execute("DELETE FROM visa_centers")
    op.execute("DELETE FROM country_rules")
    op.execute("DELETE FROM vac_operators")
    op.execute("DELETE FROM countries")
