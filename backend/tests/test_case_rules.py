from datetime import date

from app.core.cases import (
    advance,
    appeal_until,
    by_dates,
    cleared_by_outcome,
    for_group_member,
)
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


def test_without_a_result_the_dates_give_the_stage():
    today = date(2026, 11, 18)
    assert by_dates(None, today) == CaseStage.WAITING
    assert by_dates(date(2026, 11, 25), today) == CaseStage.DOCUMENTS
    assert by_dates(today, today) == CaseStage.REVIEW
    assert by_dates(date(2026, 11, 1), today) == CaseStage.REVIEW


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


# A submission group edited as one (owner's decision 2026-11-15, «Правка группы подачи»).

PURPOSE, REGISTRAR, CENTER = "purpose-tourism", "registrar-1", "center-minsk"
PLACE = {"destination_id": "PL", "submission_country_id": "BY", "visa_center_id": CENTER}
DATE = date(2026, 12, 1)


def test_a_family_takes_what_was_changed_apart_from_the_result():
    changed = {
        "purpose_id": PURPOSE,
        "registrar_id": REGISTRAR,
        "hall": "premium",
        "questionnaire_due_on": DATE,
        # Each one's own: the questionnaire received, the number, the result.
        "questionnaire_received_on": DATE,
        "vac_number": "PL-1",
        "outcome": VisaOutcome.VISA,
        "decided_on": DATE,
        "visa_entries": "multiple",
    }
    taken = for_group_member(changed, PLACE, CaseStage.REVIEW, CaseStage.REVIEW, None)
    assert taken == {
        "purpose_id": PURPOSE,
        "registrar_id": REGISTRAR,
        "hall": "premium",
        "questionnaire_due_on": DATE,
    }


def test_the_place_goes_as_a_whole():
    taken = for_group_member(
        {"visa_center_id": CENTER}, PLACE, CaseStage.WAITING, CaseStage.WAITING, None
    )
    assert taken == PLACE


def test_a_member_with_a_result_is_not_changed():
    changed = {"purpose_id": PURPOSE, "stage": CaseStage.SUBMISSION}
    for outcome in VisaOutcome:
        assert (
            for_group_member(changed, PLACE, CaseStage.DOCUMENTS, CaseStage.RESULT, outcome) == {}
        )


def test_the_stage_moves_members_that_stood_with_the_case():
    changed = {"stage": CaseStage.SUBMISSION}
    assert for_group_member(changed, PLACE, CaseStage.DOCUMENTS, CaseStage.DOCUMENTS, None) == {
        "stage": CaseStage.SUBMISSION
    }
    # A member at another stage stays where it is, as on the board.
    assert for_group_member(changed, PLACE, CaseStage.DOCUMENTS, CaseStage.WAITING, None) == {}


def test_a_result_does_not_move_the_family():
    changed = {"outcome": VisaOutcome.REFUSAL, "stage": CaseStage.RESULT}
    assert for_group_member(changed, PLACE, CaseStage.REVIEW, CaseStage.REVIEW, None) == {}
    # A case with its result, moved back by hand and now to «Результат» again: a member
    # without a result of its own does not follow it there (CRM audit 2026-11-18).
    changed = {"stage": CaseStage.RESULT}
    assert for_group_member(changed, PLACE, CaseStage.REVIEW, CaseStage.REVIEW, None) == {}


def test_the_submission_date_ends_the_wait_of_each_member():
    # The case was waiting and moved on with the date; so do the members still waiting.
    changed = {"submission_on": DATE, "stage": CaseStage.DOCUMENTS}
    assert for_group_member(changed, PLACE, CaseStage.WAITING, CaseStage.WAITING, None) == {
        "submission_on": DATE,
        "stage": CaseStage.DOCUMENTS,
    }
    # A member further on keeps its stage; a member waiting behind a case already further
    # on moves on by the usual rule.
    assert for_group_member(changed, PLACE, CaseStage.WAITING, CaseStage.SUBMISSION, None) == {
        "submission_on": DATE
    }
    assert for_group_member(
        {"submission_on": DATE}, PLACE, CaseStage.DOCUMENTS, CaseStage.WAITING, None
    ) == {"submission_on": DATE, "stage": CaseStage.DOCUMENTS}
