"""Checks of the consultation questionnaire against the trip (docs/clients-module.md →
«Анкета консультации»). Pure functions: the API passes in the destinations' passport
rules from the countries reference, the trip date and today's date.

The checks only warn; the specialist decides. Unknown data (no passport dates, no rule
for a country) gives no warning rather than a guess.
"""

import calendar
from dataclasses import dataclass
from datetime import date

from app.core.questionnaire import Questionnaire


@dataclass(frozen=True)
class Destination:
    code: str
    # From the countries reference (Schengen: issued within 10 years, valid 3 months more).
    passport_max_age_years: int | None
    passport_valid_after_months: int | None


@dataclass(frozen=True)
class Hint:
    code: str
    country: str | None = None


def add_months(day: date, months: int) -> date:
    """Calendar months; the 31st becomes the last day of a shorter month."""
    month_index = day.month - 1 + months
    year, month = day.year + month_index // 12, month_index % 12 + 1
    return date(year, month, min(day.day, calendar.monthrange(year, month)[1]))


def check(
    q: Questionnaire, destinations: list[Destination], *, trip_on: date | None, today: date
) -> list[Hint]:
    hints: list[Hint] = []
    # Without a trip date the rules are checked as if the client travelled today.
    reference = trip_on or today

    if q.passport_expires_on is not None and q.passport_expires_on < today:
        hints.append(Hint("passport_expired"))
    else:
        for d in destinations:
            if (
                q.passport_issued_on is not None
                and d.passport_max_age_years is not None
                and add_months(q.passport_issued_on, 12 * d.passport_max_age_years) < reference
            ):
                hints.append(Hint("passport_too_old", d.code))
            if (
                q.passport_expires_on is not None
                and d.passport_valid_after_months is not None
                and q.passport_expires_on < add_months(reference, d.passport_valid_after_months)
            ):
                hints.append(Hint("passport_expires_too_soon", d.code))

    refused = {r.country for r in q.refusals}
    hints += [Hint("refusal_before", d.code) for d in destinations if d.code in refused]

    if q.deportation:
        hints.append(Hint("deportation"))

    for d in destinations:
        if d.code in q.relatives_abroad:
            hints.append(Hint("invitation_relatives", d.code))
        if d.code in q.humanitarian_trips:
            hints.append(Hint("invitation_humanitarian", d.code))
    return hints
