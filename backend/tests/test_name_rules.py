"""A name in parts (ФИО) and the whole name joined from them (app/core/names.py)."""

import pytest

from app.core.names import latin_fields, name_fields, split_full_name, split_latin_name


def test_the_whole_name_is_the_parts_joined():
    assert name_fields("Ковалёва", "Анна", "Сергеевна")["full_name"] == "Ковалёва Анна Сергеевна"
    assert name_fields(None, "Анна", None)["full_name"] == "Анна"
    assert name_fields("Ковалёва", None, None)["full_name"] == "Ковалёва"
    assert latin_fields("KAVALEVA", "ANNA")["full_name_latin"] == "KAVALEVA ANNA"
    assert latin_fields(None, None)["full_name_latin"] is None


@pytest.mark.parametrize(
    ("line", "parts"),
    [
        ("Анна", (None, "Анна", None)),
        ("Ковалёва Анна", ("Ковалёва", "Анна", None)),
        ("  Ковалёва   Анна  Сергеевна ", ("Ковалёва", "Анна", "Сергеевна")),
        ("Мамедов Рашид Ахмед оглы", ("Мамедов", "Рашид", "Ахмед оглы")),
    ],
)
def test_a_name_in_one_line_is_surname_first(line: str, parts: tuple):
    fields = split_full_name(line)
    assert (fields["last_name"], fields["first_name"], fields["middle_name"]) == parts
    assert fields["full_name"] == " ".join(line.split())


@pytest.mark.parametrize(
    ("line", "parts"),
    [
        ("ANNA", (None, "ANNA")),
        ("KAVALEVA ANNA", ("KAVALEVA", "ANNA")),
        ("KAVALEVA ANNA MARIA", ("KAVALEVA", "ANNA MARIA")),
    ],
)
def test_a_latin_name_is_the_surname_and_the_given_names(line: str, parts: tuple):
    fields = split_latin_name(line)
    assert (fields["last_name_latin"], fields["first_name_latin"]) == parts
    assert fields["full_name_latin"] == line
