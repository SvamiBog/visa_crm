"""A person's name in parts: the surname, the given name and the patronymic (ФИО), and, as
in the passport, the surname and the given names in Latin. The whole name the lists show,
the search looks in and the import matches people by is joined from the parts and kept
next to them on the card (a check constraint keeps the two in step)."""

from typing import Annotated

from pydantic import Field

NAME_PARTS = ("last_name", "first_name", "middle_name")
LATIN_PARTS = ("last_name_latin", "first_name_latin")

# One part as typed in a form.
NamePart = Annotated[str, Field(max_length=100)]


def tidy(value: str | None) -> str | None:
    """A part as typed: spaces trimmed and squeezed, nothing left is None."""
    return " ".join((value or "").split()) or None


def joined(*parts: str | None) -> str | None:
    return " ".join(part for part in parts if part) or None


def name_fields(
    last_name: str | None, first_name: str | None, middle_name: str | None
) -> dict[str, str | None]:
    return {
        "last_name": last_name,
        "first_name": first_name,
        "middle_name": middle_name,
        "full_name": joined(last_name, first_name, middle_name),
    }


def latin_fields(
    last_name_latin: str | None, first_name_latin: str | None
) -> dict[str, str | None]:
    return {
        "last_name_latin": last_name_latin,
        "first_name_latin": first_name_latin,
        "full_name_latin": joined(last_name_latin, first_name_latin),
    }


def split_full_name(line: str) -> dict[str, str | None]:
    """A name in one line (a table cell, a name saved before the parts): one word is the
    given name; otherwise the surname comes first, then the given name, and the rest is
    the patronymic."""
    words = line.split()
    if len(words) <= 1:
        return name_fields(None, words[0] if words else None, None)
    return name_fields(words[0], words[1], " ".join(words[2:]) or None)


def split_latin_name(line: str) -> dict[str, str | None]:
    """As in the passport: the surname, then the given names; one word is the given name."""
    words = line.split()
    if len(words) <= 1:
        return latin_fields(None, words[0] if words else None)
    return latin_fields(words[0], " ".join(words[1:]))
