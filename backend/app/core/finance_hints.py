"""What the lists fill in an empty field of a case's finances, and what the specialist took
off (backlog 1.15.12; owner's answer 2026-11-24).

The form «Стоимость и условия» starts an empty field from the lists and the consultation:
the registrar's talon, the list's prepayment, the consultation credit, the partner's terms.
A field the specialist emptied used to be filled in again the next time the form opened,
so the next save brought it back. Now a case remembers each field taken off, and the lists
do not fill it in again until a value is set (typed, charged in the settlements or loaded
from a file).
"""

from collections.abc import Iterable
from typing import Any

from app.models import CaseFinance

# A suggested field of the finances → the flag that says it was taken off.
TAKEN_OFF = {
    "registrar_fee_amount": "registrar_fee_removed",
    "prepayment": "prepayment_removed",
    "consultation_credit": "consultation_credit_removed",
    "partner_terms": "partner_terms_removed",
}


def holds(value: Any) -> bool:
    """The field has a value: a consultation credit of 0 is none."""
    return value is not None and value != 0


def held(finance: CaseFinance) -> set[str]:
    """The suggested fields that have a value now."""
    return {field for field in TAKEN_OFF if holds(getattr(finance, field))}


def taken_off(finance: CaseFinance | None) -> set[str]:
    """The fields the lists must not fill in for this case."""
    if finance is None:
        return set()
    return {field for field, flag in TAKEN_OFF.items() if getattr(finance, flag)}


def note(finance: CaseFinance, emptied: Iterable[str]) -> None:
    """After a change: a field with a value is not taken off; one of `emptied` (it held a
    value, or the lists offered one, and is empty now) is."""
    emptied = set(emptied)
    for field, flag in TAKEN_OFF.items():
        if holds(getattr(finance, field)):
            setattr(finance, flag, False)
        elif field in emptied:
            setattr(finance, flag, True)
