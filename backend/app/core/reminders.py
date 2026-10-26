"""In-CRM reminders, «Требует внимания» (docs/clients-module.md → «Напоминания»).

On stage 1 reminders are a computed list, not messages. Each kind has trigger days —
«3 days before the submission and the day before» — and shows from the latest trigger
that has come until its event is over. «Готово» hides that occurrence only (its key
names the trigger day), so the next trigger brings the reminder back. The windows are
the specialist's settings; these are the defaults of the spec.
"""

import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from enum import StrEnum

from app.core.consultation_checks import add_months


class ReminderKind(StrEnum):
    BIRTHDAY = "birthday"
    SUBMISSION = "submission"
    BALANCE_DUE = "balance_due"
    APPLICATION_DEADLINE = "application_deadline"
    BOOKING_DEADLINE = "booking_deadline"
    THINKING = "thinking"
    VISA_EXPIRES = "visa_expires"
    APPEAL = "appeal"
    REGISTRAR_ADVANCE = "registrar_advance"
    # An employee went over a daily limit (backlog Т.14); for the specialist only.
    CARD_LIMIT = "card_limit"
    PASSWORD_LIMIT = "password_limit"


@dataclass(frozen=True)
class Settings:
    submission_days: int = 3  # and the day before
    balance_days: int = 3  # before the submission
    application_days: int = 5  # before the window's deadline, and on the day
    booking_days: int = 3  # before free cancellation ends, and the day before
    thinking_months: tuple[int, ...] = (1, 3)  # after the consultation
    visa_expiry_months: int = 2  # before the visa ends
    appeal_days: int = 3  # right after a refusal, and this many days before the end


def occurrence(triggers: Iterable[date], today: date, until: date | None = None) -> date | None:
    """The latest trigger day that has come, while the event is not over."""
    if until is not None and today > until:
        return None
    return max((d for d in triggers if d <= today), default=None)


def submission(submission_on: date, today: date, s: Settings) -> date | None:
    triggers = (submission_on - timedelta(days=s.submission_days), submission_on - timedelta(1))
    return occurrence(triggers, today, until=submission_on)


def balance_due(
    submission_on: date, balance: Decimal | None, today: date, s: Settings
) -> date | None:
    """The client still owes money a few days before the submission."""
    if balance is None or balance <= 0:
        return None
    return occurrence([submission_on - timedelta(days=s.balance_days)], today, until=submission_on)


def application_deadline(deadline_on: date, today: date, s: Settings) -> date | None:
    """The end of an application window (France, 60 days): stays while the attempt is open."""
    return occurrence([deadline_on - timedelta(days=s.application_days), deadline_on], today)


def booking_deadline(deadline: date, today: date, s: Settings) -> date | None:
    triggers = (deadline - timedelta(days=s.booking_days), deadline - timedelta(1))
    return occurrence(triggers, today, until=deadline)


def thinking(held_on: date, today: date, s: Settings) -> date | None:
    """A client still thinking after the consultation: call them again. The last reminder
    stays a month, so long-forgotten consultations do not fill the list."""
    until = add_months(held_on, max(s.thinking_months) + 1)
    return occurrence([add_months(held_on, m) for m in s.thinking_months], today, until=until)


def visa_expires(valid_to: date, today: date, s: Settings) -> date | None:
    """A reason to offer a new visa."""
    return occurrence([add_months(valid_to, -s.visa_expiry_months)], today, until=valid_to)


def appeal(decided_on: date, appeal_until: date, today: date, s: Settings) -> date | None:
    triggers = (decided_on, appeal_until - timedelta(days=s.appeal_days))
    return occurrence(triggers, today, until=appeal_until)


def birthday_in(birth_date: date, year: int) -> date:
    """The birthday in a year; the 29th of February is the 28th in other years."""
    if birth_date.month == 2 and birth_date.day == 29:
        try:
            return date(year, 2, 29)
        except ValueError:
            return date(year, 2, 28)
    return birth_date.replace(year=year)


def birthday(birth_date: date, today: date) -> date | None:
    return today if birthday_in(birth_date, today.year) == today else None


def key(kind: ReminderKind, subject: uuid.UUID | str, occurs_on: date) -> str:
    """Names one occurrence by ids and a day only: no client data."""
    return f"{kind.value}:{subject}:{occurs_on.isoformat()}"


def parse_key(value: str) -> tuple[ReminderKind, str, date] | None:
    try:
        kind, subject, day = value.split(":")
        return ReminderKind(kind), str(uuid.UUID(subject)), date.fromisoformat(day)
    except ValueError:
        return None
