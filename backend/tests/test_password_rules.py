"""The password generator follows each site's rules (docs/clients-module.md → «Сейф»)."""

import pytest

from app.core.passwords import DIGITS, LOWER, UPPER, PasswordRules, generate_password

ALL = PasswordRules(length=12, lower=True, upper=True, digits=True, symbols="!@#")


def test_has_the_length_and_every_required_kind_of_character():
    for _ in range(200):
        password = generate_password(ALL)
        assert len(password) == 12
        assert any(c in LOWER for c in password)
        assert any(c in UPPER for c in password)
        assert any(c in DIGITS for c in password)
        assert any(c in "!@#" for c in password)


def test_uses_only_the_characters_the_site_accepts():
    rules = PasswordRules(length=20, lower=False, upper=True, digits=True, symbols="")
    for _ in range(100):
        password = generate_password(rules)
        assert set(password) <= set(UPPER + DIGITS)


def test_leaves_out_characters_easy_to_mistype():
    alphabet = LOWER + UPPER + DIGITS
    assert not set("0O1lI") & set(alphabet)


def test_passwords_differ():
    assert len({generate_password(ALL) for _ in range(50)}) == 50


def test_rules_that_cannot_be_met_are_rejected():
    with pytest.raises(ValueError):
        generate_password(PasswordRules(length=3, lower=True, upper=True, digits=True, symbols="!"))
    with pytest.raises(ValueError):
        generate_password(
            PasswordRules(length=12, lower=False, upper=False, digits=False, symbols="")
        )
