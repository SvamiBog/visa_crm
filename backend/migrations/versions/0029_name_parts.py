"""name parts: the surname, the given name and the patronymic of a contact, entered apart

The card keeps the parts (ФИО, and the Latin surname and given names as in the passport);
full_name and full_name_latin become the parts joined, for the lists, the search and the
import (app/core/names.py). Names saved before are split by the import's rule for a name
in one line: one word is the given name; otherwise the surname, the given name and the
rest as the patronymic (in Latin, the rest are the given names).

Revision ID: 0029
Revises: 0028
Create Date: 2026-10-24

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0029"
down_revision: str | None = "0028"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PARTS = ("last_name", "first_name", "middle_name", "last_name_latin", "first_name_latin")


def upgrade() -> None:
    for column in PARTS:
        op.add_column("contacts", sa.Column(column, sa.String(length=200), nullable=True))
    # Three parts of up to 200 each no longer fit in 200: no rewrite, varchar → text.
    op.alter_column("contacts", "full_name", type_=sa.Text(), existing_nullable=False)
    op.alter_column("contacts", "full_name_latin", type_=sa.Text(), existing_nullable=True)

    op.execute(
        r"""
        UPDATE contacts SET
            full_name = regexp_replace(btrim(full_name), '\s+', ' ', 'g'),
            full_name_latin = nullif(regexp_replace(btrim(full_name_latin), '\s+', ' ', 'g'), '')
        """
    )
    op.execute(
        """
        UPDATE contacts SET
            last_name = CASE WHEN cardinality(w) > 1 THEN w[1] END,
            first_name = CASE WHEN cardinality(w) > 1 THEN w[2] ELSE w[1] END,
            middle_name = nullif(array_to_string(w[3:], ' '), '')
        FROM (SELECT id, string_to_array(full_name, ' ') AS w FROM contacts) AS words
        WHERE contacts.id = words.id
        """
    )
    op.execute(
        """
        UPDATE contacts SET
            last_name_latin = CASE WHEN cardinality(w) > 1 THEN w[1] END,
            first_name_latin = CASE WHEN cardinality(w) > 1
                THEN array_to_string(w[2:], ' ') ELSE w[1] END
        FROM (
            SELECT id, string_to_array(full_name_latin, ' ') AS w
            FROM contacts WHERE full_name_latin IS NOT NULL
        ) AS words
        WHERE contacts.id = words.id
        """
    )

    op.create_check_constraint(
        op.f("ck_contacts_has_name"),
        "contacts",
        "last_name IS NOT NULL OR first_name IS NOT NULL",
    )
    op.create_check_constraint(
        op.f("ck_contacts_full_name_joined"),
        "contacts",
        "full_name = concat_ws(' ', last_name, first_name, middle_name)",
    )
    op.create_check_constraint(
        op.f("ck_contacts_full_name_latin_joined"),
        "contacts",
        "full_name_latin IS NOT DISTINCT FROM "
        "nullif(concat_ws(' ', last_name_latin, first_name_latin), '')",
    )


def downgrade() -> None:
    for name in ("full_name_latin_joined", "full_name_joined", "has_name"):
        op.drop_constraint(op.f(f"ck_contacts_{name}"), "contacts", type_="check")
    # The whole names are kept as they are; anything past 200 characters is cut.
    op.execute("UPDATE contacts SET full_name = left(full_name, 200)")
    op.execute("UPDATE contacts SET full_name_latin = left(full_name_latin, 200)")
    op.alter_column("contacts", "full_name_latin", type_=sa.String(length=200))
    op.alter_column("contacts", "full_name", type_=sa.String(length=200), existing_nullable=False)
    for column in reversed(PARTS):
        op.drop_column("contacts", column)
