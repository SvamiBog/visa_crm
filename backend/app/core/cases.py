"""Case rules (docs/clients-module.md → «Этапы кейса», «Результат визы»). Pure functions:
the API applies them to a case after a change.
"""

from collections.abc import Mapping
from datetime import date, timedelta
from enum import StrEnum
from typing import Any

from app.core.consultation_checks import add_months
from app.models.visa_case import CaseStage, VisaOutcome

VISA_DETAILS = ("visa_entries", "visa_valid_from", "visa_valid_to", "stay_days")


class ResultPeriod(StrEnum):
    """Which decisions the «Результат» column of the board shows (owner's request
    2026-11-19, docs/decisions.md → «Период колонки «Результат»»): the last month, half a
    year or year, all of them, or the specialist's own dates."""

    MONTH = "month"
    HALF_YEAR = "half_year"
    YEAR = "year"
    ALL = "all"
    DATES = "dates"


# How far back each quick choice reaches, in calendar months.
RESULT_PERIOD_MONTHS = {ResultPeriod.MONTH: 1, ResultPeriod.HALF_YEAR: 6, ResultPeriod.YEAR: 12}

# What a submission group has in common (owner's decision 2026-11-15, docs/decisions.md →
# «Правка группы подачи»); the result, the questionnaire received and money are each
# one's own.
GROUP_FIELDS = (
    "destination_id",
    "submission_country_id",
    "visa_center_id",
    "purpose_id",
    "registrar_id",
    "hall",
    "submission_on",
    "questionnaire_due_on",
)
# A visa center belongs to its pair of countries, so the place goes as a whole.
PLACE_FIELDS = ("destination_id", "submission_country_id", "visa_center_id")


def advance(stage: CaseStage, changes: Mapping[str, Any]) -> CaseStage:
    """The case moves on by itself when a result or an appointment date is entered:
    the date ends the wait (documents are collected after it comes), a result ends the
    case. It never moves back by itself; the specialist moves it by hand."""
    if changes.get("outcome") is not None:
        return CaseStage.RESULT
    if changes.get("submission_on") is not None and stage == CaseStage.WAITING:
        return CaseStage.DOCUMENTS
    return stage


def by_dates(submission_on: date | None, today: date) -> CaseStage:
    """Where a case without a result stands by its dates: waiting for an appointment,
    collecting documents before it, under review once its day has come. «Результат» needs
    a result (CRM audit 2026-11-18), so a case whose result is taken away goes back here."""
    if submission_on is None:
        return CaseStage.WAITING
    return CaseStage.REVIEW if submission_on <= today else CaseStage.DOCUMENTS


def for_group_member(
    changed: Mapping[str, Any],
    place: Mapping[str, Any],
    stage_before: CaseStage,
    member_stage: CaseStage,
    member_outcome: VisaOutcome | None,
) -> dict[str, Any]:
    """What another member of the submission group takes from a change to one case.

    `changed` holds the fields the case changed, with their new values, `place` its
    place after the change, `stage_before` its stage before it. Only what was changed
    goes on, and only to a member without a result: its case is over. The stage follows
    as on the board, for a member that stood where the case stood, but not when a result
    moved the case, and never into «Результат», which needs the member's own result;
    otherwise the member moves on by the usual rule (`advance`)."""
    if member_outcome is not None:
        return {}
    taken = {f: v for f, v in changed.items() if f in GROUP_FIELDS}
    if any(f in changed for f in PLACE_FIELDS):
        taken |= {f: place[f] for f in PLACE_FIELDS}
    if (
        "stage" in changed
        and changed["stage"] != CaseStage.RESULT
        and changed.get("outcome") is None
        and member_stage == stage_before
    ):
        taken["stage"] = changed["stage"]
    elif (stage := advance(member_stage, taken)) != member_stage:
        taken["stage"] = stage
    return taken


def cleared_by_outcome(outcome: VisaOutcome | None) -> dict[str, Any]:
    """Result fields that do not apply to this outcome, with their empty values: visa
    details only for a visa, an appeal only after a decision, a decision date only with
    a result."""
    cleared: dict[str, Any] = {}
    if outcome != VisaOutcome.VISA:
        cleared |= dict.fromkeys(VISA_DETAILS)
    if outcome not in (VisaOutcome.VISA, VisaOutcome.REFUSAL):
        cleared["after_appeal"] = False
    if outcome is None:
        cleared["decided_on"] = None
    return cleared


def appeal_until(
    outcome: VisaOutcome | None,
    decided_on: date | None,
    after_appeal: bool,
    deadline_days: int | None,
) -> date | None:
    """Last day to appeal a refusal, where the consulate accepts appeals (deadline from
    the country rules). None when there is nothing to appeal or the deadline is unknown."""
    if outcome != VisaOutcome.REFUSAL or after_appeal or decided_on is None:
        return None
    if deadline_days is None:
        return None
    return decided_on + timedelta(days=deadline_days)


def result_window(
    period: ResultPeriod, today: date, start: date | None = None, end: date | None = None
) -> tuple[date | None, date | None]:
    """First and last day of decision the «Результат» column shows; None leaves that end
    open. `start` and `end` are the specialist's own dates, taken only with `DATES`."""
    if period == ResultPeriod.DATES:
        return start, end
    if period == ResultPeriod.ALL:
        return None, None
    return add_months(today, -RESULT_PERIOD_MONTHS[period]), None
