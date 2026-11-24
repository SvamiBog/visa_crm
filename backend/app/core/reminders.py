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
    # What the specialist reminded clients of by hand (backlog 1.19, question 4).
    CONSULTATION = "consultation"
    FOLLOW_UP = "follow_up"
    CONTRACT = "contract"
    QUESTIONNAIRE = "questionnaire"
    BALANCE_QUEUED = "balance_queued"
    # Documents not collected before the submission (backlog 2.3).
    PACKAGE = "package"
    # The submission is long past and no result is entered (CRM audit 2026-11-18).
    RESULT = "result"
    # The passport's data goes soon after the decision (backlog 2.8, app/core/passport_purge.py).
    PASSPORT_PURGE = "passport_purge"
    # The client uploaded documents through the portal: to accept or return (backlog 2.9).
    PORTAL_UPLOADS = "portal_uploads"
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
    consultation_days: int = 1  # before the consultation, and on the day
    contract_days: int = 3  # after the case is opened
    questionnaire_days: int = 1  # before the questionnaire is due, and on the day
    package_days: int = 7  # before «Документы до» or the submission, and the day before
    # After the submission, until a result is entered: the decision usually comes in two
    # weeks (the specialist's answer to question 34, 2026-11-24).
    result_days: int = 14


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


def consultation(held_on: date, today: date, s: Settings) -> date | None:
    """A consultation ahead: the call is not to be forgotten — the day before and on the
    day. Once held it goes: the client's decision is asked for only on the day to hear it
    that the specialist set herself (`follow_up`, pilot fix 3, 2026-11-19)."""
    triggers = (held_on - timedelta(days=s.consultation_days), held_on)
    return occurrence(triggers, today, until=held_on)


def follow_up(follow_up_on: date, today: date) -> date | None:
    """The day agreed at the consultation to hear the client's decision. Like a thinking
    client, it stays a month and goes."""
    return occurrence([follow_up_on], today, until=add_months(follow_up_on, 1))


def contract_unsigned(opened_on: date, today: date, s: Settings) -> date | None:
    """A case is open and the client has signed no contract. It stays a month, so old
    cases brought from the tables do not fill the list."""
    trigger = opened_on + timedelta(days=s.contract_days)
    return occurrence([trigger], today, until=add_months(trigger, 1))


def questionnaire(due_on: date, today: date, s: Settings) -> date | None:
    """The specialist's questionnaire for the application form: stays until received."""
    return occurrence([due_on - timedelta(days=s.questionnaire_days), due_on], today)


def balance_queued(
    queued_on: date,
    balance: Decimal | None,
    submission_on: date | None,
    today: date,
    s: Settings,
) -> date | None:
    """The client pays the rest once put in the queue. Before the submission the reminder
    of `balance_due` takes over, so the two never show together."""
    if balance is None or balance <= 0:
        return None
    until = None
    if submission_on is not None:
        until = submission_on - timedelta(days=s.balance_days + 1)
    return occurrence([queued_on], today, until=until)


def package_due_on(submission_on: date | None, documents_due_on: date | None) -> date | None:
    """The day the documents are due by: the specialist's «Документы до», or the
    submission when it comes sooner (a slot moved forward) or no day is set."""
    days = [d for d in (submission_on, documents_due_on) if d is not None]
    return min(days, default=None)


def package(
    submission_on: date | None,
    ready: bool,
    today: date,
    s: Settings,
    *,
    documents_due_on: date | None = None,
) -> date | None:
    """Documents still to collect before the submission (the package check, 2.3): a week
    and the day before. The day the specialist named the client for the documents
    («Документы до», question 26) is a real deadline, like the questionnaire's: counted
    from it, the reminder stays overdue after it. Without it, or when the submission comes
    sooner, the submission is the day. Either way the reminder is over with the
    submission."""
    if ready:
        return None
    due_on = package_due_on(submission_on, documents_due_on)
    if due_on is None:
        return None
    triggers = (due_on - timedelta(days=s.package_days), due_on - timedelta(1))
    return occurrence(triggers, today, until=submission_on)


def result(submission_on: date, today: date, s: Settings) -> date | None:
    """The submission was long ago and no result is entered: the funnel, the approval
    rate and the reminders that follow a result (appeal, the visa expiring) wait for it.
    Stays until the result is in."""
    return occurrence([submission_on + timedelta(days=s.result_days)], today)


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
