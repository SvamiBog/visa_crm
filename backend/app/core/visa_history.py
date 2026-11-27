"""The client's visa history (docs/clients-module.md → «Анкета консультации», backlog A3):
the visas written in the questionnaire and the visas granted in the client's own cases, as
one list. Pure functions: the API passes in the questionnaire's visas, the cases with the
outcome «visa» and today's date in the specialist's time zone.

Nothing is stored: a case's visa is read from its result each time, so changing or taking
back the result changes the history at once."""

import uuid
from dataclasses import dataclass
from datetime import date
from typing import Literal

from app.core.questionnaire import VisaKind, VisaRecord
from app.models import PurposeKind

VisaStatus = Literal["active", "expired"]

# A visa for study or work in a Schengen country is often a national one (D), sometimes a
# short-stay one (C); the case does not keep which, so the kind is left unsaid.
_LONG_STAY_PURPOSES = frozenset({PurposeKind.STUDY, PurposeKind.WORK})
_KIND_OF_COUNTRY: dict[str, VisaKind] = {"GB": "uk", "US": "usa"}


@dataclass(frozen=True)
class CaseVisa:
    """A case with the outcome «visa»: the country and the purpose are the case's, the
    term is its result's."""

    case_id: uuid.UUID
    number: int
    # ISO code of the visa's country and the day it joined the Schengen area, if it has.
    country: str
    schengen_since: date | None
    purpose: PurposeKind | None
    valid_from: date | None
    valid_to: date | None
    decided_on: date | None


@dataclass(frozen=True)
class HistoryVisa:
    """A line of the history. `case_id` and `case_number` are set on a visa from a case."""

    kind: VisaKind | None
    country: str | None
    purpose: PurposeKind | None
    valid_from: date | None
    valid_to: date | None
    note: str | None
    status: VisaStatus | None
    case_id: uuid.UUID | None = None
    case_number: int | None = None


def visa_status(valid_from: date | None, valid_to: date | None, today: date) -> VisaStatus | None:
    """«Действует» through the visa's last day, «истекла» from the day after. A visa with
    no last day, or one that has not started yet, gets no mark rather than a guess."""
    if valid_to is None:
        return None
    if valid_to < today:
        return "expired"
    if valid_from is None or valid_from <= today:
        return "active"
    return None


def case_visa_kind(visa: CaseVisa, today: date) -> VisaKind | None:
    """What kind of visa a case's country gives: Schengen for a country in the area on the
    day the visa starts, the UK and the USA by the country. None where the country and the
    purpose do not say."""
    by_country = _KIND_OF_COUNTRY.get(visa.country)
    if by_country is not None:
        return by_country
    starts = visa.valid_from or visa.decided_on or today
    in_schengen = visa.schengen_since is not None and visa.schengen_since <= starts
    if in_schengen and visa.purpose not in _LONG_STAY_PURPOSES:
        return "schengen"
    return None


def visa_history(
    written: list[VisaRecord], granted: list[CaseVisa], *, today: date
) -> list[HistoryVisa]:
    """The visas written in the questionnaire in their order, then the cases' visas by
    case number. A visa the specialist also wrote by hand — the same country and the same
    term — is one line, the case's, with the kind, the purpose and the note she wrote."""
    left = list(written)
    lines: list[HistoryVisa] = []
    for visa in sorted(granted, key=lambda v: v.number):
        hers = next((w for w in left if _same_visa(w, visa)), None)
        if hers is not None:
            left.remove(hers)
        lines.append(
            HistoryVisa(
                kind=hers.kind if hers else case_visa_kind(visa, today),
                country=visa.country,
                purpose=(hers.purpose if hers else None) or visa.purpose,
                valid_from=visa.valid_from,
                valid_to=visa.valid_to,
                note=hers.note if hers else None,
                status=visa_status(visa.valid_from, visa.valid_to, today),
                case_id=visa.case_id,
                case_number=visa.number,
            )
        )
    return [
        HistoryVisa(
            kind=w.kind,
            country=w.country,
            purpose=w.purpose,
            valid_from=w.valid_from,
            valid_to=w.valid_to,
            note=w.note,
            status=visa_status(w.valid_from, w.valid_to, today),
        )
        for w in left
    ] + lines


def _same_visa(written: VisaRecord, visa: CaseVisa) -> bool:
    """The country and the term are the same, and the last day is known: without a term
    there is nothing to tell one visa of a country from another by."""
    return (
        visa.valid_to is not None
        and written.country == visa.country
        and written.valid_from == visa.valid_from
        and written.valid_to == visa.valid_to
    )
