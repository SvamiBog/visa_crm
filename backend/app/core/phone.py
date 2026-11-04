"""Phone numbers in one form, so the duplicate guard sees "+375 (29) 123-45-67" and
"375291234567" as the same number. Stored as E.164: "+" and 8–15 digits.

Belarusian numbers typed the local way («8 029 …», «029 …», «29 …») get +375: clients give
their number as they are used to (specialist run before the pilot, 2026-11-04). The
VisaCRM import format still wants the country code (app/core/data_values.py)."""

import re

_DIGITS = re.compile(r"\d")
# Mobile operators' codes: only with one of them are nine bare digits taken as Belarusian.
BY_MOBILE_CODES = ("25", "29", "33", "44")


def _belarusian(digits: str) -> str | None:
    """The national number (9 digits) of a Belarusian number typed without +375."""
    if len(digits) == 11 and digits.startswith("80"):  # «8 0…»: no country code starts so
        return digits[2:]
    if len(digits) == 10 and digits.startswith("0"):  # nor with 0
        return digits[1:]
    if len(digits) == 9 and digits.startswith(BY_MOBILE_CODES):
        return digits
    return None


def normalize_phone(value: str) -> str:
    """Keep the digits, add "+". A number typed with "+" or "00" keeps its country code;
    a Belarusian one typed the local way gets 375.
    Raises ValueError for anything that is not 8–15 digits."""
    raw = value.strip()
    international = raw.startswith(("+", "00"))
    if raw.startswith("00"):  # international prefix written as 00
        raw = raw[2:]
    digits = "".join(_DIGITS.findall(raw))
    if not international and (national := _belarusian(digits)) is not None:
        digits = "375" + national
    if not 8 <= len(digits) <= 15:
        raise ValueError("phone must have 8 to 15 digits with the country code")
    return f"+{digits}"


def phone_digits(value: str) -> str:
    """Digits typed in a search box, to match part of a stored number."""
    return "".join(_DIGITS.findall(value))


def phone_search_digits(value: str) -> set[str]:
    """Digits to look for in stored numbers: as typed, and without a local prefix
    («8 0» or «0» before a mobile code), since the stored number has 375 there."""
    digits = phone_digits(value)
    forms = {digits}
    if value.strip().startswith("+"):
        return forms
    for prefix in ("80", "0"):
        rest = digits.removeprefix(prefix)
        if rest != digits and rest.startswith(BY_MOBILE_CODES):
            forms.add(rest)
            break
    return forms
