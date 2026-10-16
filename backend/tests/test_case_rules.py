from datetime import date

from app.core.cases import advance, appeal_until, cleared_by_outcome
from app.models import CaseStage, VisaOutcome


def test_appointment_date_ends_the_wait():
    assert advance(CaseStage.WAITING, {"submission_on": date(2026, 11, 20)}) == (
        CaseStage.DOCUMENTS
    )
    # Later stages are not moved back, and clearing the date moves nothing.
    assert advance(CaseStage.REVIEW, {"submission_on": date(2026, 11, 20)}) == CaseStage.REVIEW
    assert advance(CaseStage.WAITING, {"submission_on": None}) == CaseStage.WAITING


def test_result_ends_the_case():
    assert advance(CaseStage.DOCUMENTS, {"outcome": VisaOutcome.REFUSAL}) == CaseStage.RESULT
    assert advance(CaseStage.RESULT, {"outcome": None}) == CaseStage.RESULT
    assert advance(CaseStage.WAITING, {"hall": "premium"}) == CaseStage.WAITING


def test_outcome_clears_what_does_not_apply():
    assert cleared_by_outcome(VisaOutcome.VISA) == {}
    refusal = cleared_by_outcome(VisaOutcome.REFUSAL)
    assert refusal == dict.fromkeys(
        ("visa_entries", "visa_valid_from", "visa_valid_to", "stay_days")
    )
    assert cleared_by_outcome(VisaOutcome.NOT_SUBMITTED)["after_appeal"] is False
    assert cleared_by_outcome(None)["decided_on"] is None


def test_appeal_deadline():
    decided = date(2026, 10, 1)
    assert appeal_until(VisaOutcome.REFUSAL, decided, False, 14) == date(2026, 10, 15)
    assert appeal_until(VisaOutcome.REFUSAL, decided, True, 14) is None  # already appealed
    assert appeal_until(VisaOutcome.REFUSAL, None, False, 14) is None  # date unknown
    assert appeal_until(VisaOutcome.REFUSAL, decided, False, None) is None  # rule unknown
    assert appeal_until(VisaOutcome.VISA, decided, False, 14) is None
