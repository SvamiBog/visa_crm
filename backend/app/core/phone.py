"""Phone numbers in one form, so the duplicate guard sees "+375 (29) 123-45-67" and
"375291234567" as the same number. Stored as E.164: "+" and 8–15 digits."""

import re

_DIGITS = re.compile(r"\d")


def normalize_phone(value: str) -> str:
    """Keep the digits, add "+". The number must include the country code.
    Raises ValueError for anything that is not 8–15 digits."""
    raw = value.strip()
    if raw.startswith("00"):  # international prefix written as 00
        raw = raw[2:]
    digits = "".join(_DIGITS.findall(raw))
    if not 8 <= len(digits) <= 15:
        raise ValueError("phone must have 8 to 15 digits with the country code")
    return f"+{digits}"


def phone_digits(value: str) -> str:
    """Digits typed in a search box, to match part of a stored number."""
    return "".join(_DIGITS.findall(value))
