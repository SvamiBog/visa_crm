"""Case rules (docs/clients-module.md → «Этапы кейса», «Результат визы»). Pure functions:
the API applies them to a case after a change.
"""

from collections.abc import Mapping
from datetime import date, timedelta
from typing import Any

from app.models.visa_case import CaseStage, VisaOutcome

VISA_DETAILS = ("visa_entries", "visa_valid_from", "visa_valid_to", "stay_days")


def advance(stage: CaseStage, changes: Mapping[str, Any]) -> CaseStage:
    """The case moves on by itself when a result or an appointment date is entered:
    the date ends the wait (documents are collected after it comes), a result ends the
    case. It never moves back by itself; the specialist moves it by hand."""
    if changes.get("outcome") is not None:
        return CaseStage.RESULT
    if changes.get("submission_on") is not None and stage == CaseStage.WAITING:
        return CaseStage.DOCUMENTS
    return stage


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
