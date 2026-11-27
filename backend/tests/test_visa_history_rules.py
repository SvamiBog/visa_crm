"""The client's visa history (docs/clients-module.md → «Анкета консультации», backlog A3):
pure rules."""

from datetime import date
from uuid import UUID

from app.core.questionnaire import VisaRecord
from app.core.visa_history import CaseVisa, case_visa_kind, visa_history, visa_status
from app.models import PurposeKind

TODAY = date(2026, 11, 27)
POLAND_SINCE = date(2007, 12, 21)
BULGARIA_SINCE = date(2024, 3, 31)


def granted(number: int = 7, country: str = "PL", **fields) -> CaseVisa:
    values = {
        "case_id": UUID(int=number),
        "number": number,
        "country": country,
        "schengen_since": POLAND_SINCE if country == "PL" else None,
        "purpose": PurposeKind.TOURISM,
        "valid_from": date(2026, 12, 10),
        "valid_to": date(2027, 12, 9),
        "decided_on": date(2026, 12, 1),
    }
    return CaseVisa(**{**values, **fields})


def test_a_visa_is_valid_through_its_last_day_and_expired_the_day_after():
    start = date(2026, 1, 1)
    assert visa_status(start, date(2026, 11, 27), TODAY) == "active"
    assert visa_status(start, date(2026, 11, 26), TODAY) == "expired"
    # The first day counts too.
    assert visa_status(TODAY, date(2027, 1, 1), TODAY) == "active"


def test_a_visa_that_has_not_started_or_has_no_last_day_gets_no_mark():
    assert visa_status(date(2026, 11, 28), date(2027, 11, 27), TODAY) is None
    assert visa_status(date(2026, 1, 1), None, TODAY) is None
    assert visa_status(None, None, TODAY) is None
    # Only the last day is known: it alone decides.
    assert visa_status(None, date(2026, 11, 27), TODAY) == "active"
    assert visa_status(None, date(2026, 11, 26), TODAY) == "expired"


def test_the_kind_comes_from_the_country_of_the_case():
    assert case_visa_kind(granted(country="PL"), TODAY) == "schengen"
    assert case_visa_kind(granted(country="GB"), TODAY) == "uk"
    assert case_visa_kind(granted(country="US"), TODAY) == "usa"
    # China, say: the country alone does not name a kind.
    assert case_visa_kind(granted(country="CN"), TODAY) is None


def test_a_country_gives_schengen_visas_from_the_day_it_joined():
    bulgaria = {"country": "BG", "schengen_since": BULGARIA_SINCE}
    before = granted(**bulgaria, valid_from=date(2024, 3, 30), valid_to=date(2024, 6, 1))
    since = granted(**bulgaria, valid_from=date(2024, 3, 31), valid_to=date(2024, 6, 1))
    assert case_visa_kind(before, TODAY) is None
    assert case_visa_kind(since, TODAY) == "schengen"
    # No first day in the result: the day of the decision, then today.
    by_decision = {"valid_from": None, "valid_to": None}
    assert (
        case_visa_kind(granted(**bulgaria, **by_decision, decided_on=date(2024, 3, 1)), TODAY)
        is None
    )
    assert case_visa_kind(granted(**bulgaria, **by_decision, decided_on=None), TODAY) == "schengen"


def test_a_visa_for_study_or_work_in_a_schengen_country_has_no_kind():
    # It may be a national one: the case does not keep which.
    assert case_visa_kind(granted(purpose=PurposeKind.STUDY), TODAY) is None
    assert case_visa_kind(granted(purpose=PurposeKind.WORK), TODAY) is None
    assert case_visa_kind(granted(purpose=PurposeKind.BUSINESS), TODAY) == "schengen"
    assert case_visa_kind(granted(purpose=None), TODAY) == "schengen"
    # The UK's kind does not depend on the purpose.
    assert case_visa_kind(granted(country="GB", purpose=PurposeKind.STUDY), TODAY) == "uk"


def test_a_case_visa_is_a_line_with_its_case_term_and_mark():
    [line] = visa_history([], [granted(valid_from=date(2026, 6, 1))], today=TODAY)
    assert (line.country, line.kind, line.purpose) == ("PL", "schengen", PurposeKind.TOURISM)
    assert (line.valid_from, line.valid_to) == (date(2026, 6, 1), date(2027, 12, 9))
    assert line.status == "active"
    assert (line.case_id, line.case_number) == (UUID(int=7), 7)
    assert line.note is None


def test_a_case_visa_without_dates_is_still_in_the_history():
    [line] = visa_history([], [granted(valid_from=None, valid_to=None)], today=TODAY)
    assert (line.country, line.case_number, line.status) == ("PL", 7, None)


def test_written_visas_come_first_then_the_cases_by_number():
    written = [
        VisaRecord(kind="usa", valid_to=date(2030, 1, 1), note="B1/B2"),
        VisaRecord(kind="schengen", country="IT", valid_to=date(2024, 5, 1)),
    ]
    cases = [granted(11, valid_to=date(2026, 1, 1)), granted(7, country="GB")]
    lines = visa_history(written, cases, today=TODAY)
    assert [(v.country, v.case_number) for v in lines] == [
        (None, None),
        ("IT", None),
        ("GB", 7),
        ("PL", 11),
    ]
    assert [v.status for v in lines] == ["active", "expired", None, "expired"]
    # A written visa stays as it was written.
    assert (lines[0].kind, lines[0].note, lines[0].case_id) == ("usa", "B1/B2", None)


def test_a_visa_written_by_hand_and_granted_in_a_case_is_one_line():
    same = VisaRecord(
        kind="national",
        country="PL",
        purpose=PurposeKind.WORK,
        valid_from=date(2026, 12, 10),
        valid_to=date(2027, 12, 9),
        note="рабочая, воеводское приглашение",
    )
    [line] = visa_history([same], [granted()], today=TODAY)
    assert line.case_number == 7
    # What the specialist wrote herself says more than the case does.
    assert (line.kind, line.purpose) == ("national", PurposeKind.WORK)
    assert line.note == "рабочая, воеводское приглашение"


def test_a_written_visa_without_a_purpose_takes_the_purpose_of_the_case():
    same = VisaRecord(
        kind="schengen", country="PL", valid_from=date(2026, 12, 10), valid_to=date(2027, 12, 9)
    )
    [line] = visa_history([same], [granted()], today=TODAY)
    assert (line.kind, line.purpose, line.case_number) == ("schengen", PurposeKind.TOURISM, 7)


def test_another_country_or_term_is_another_visa():
    other_term = VisaRecord(
        kind="schengen", country="PL", valid_from=date(2025, 12, 10), valid_to=date(2026, 12, 9)
    )
    other_country = VisaRecord(
        kind="schengen", country="LT", valid_from=date(2026, 12, 10), valid_to=date(2027, 12, 9)
    )
    lines = visa_history([other_term, other_country], [granted()], today=TODAY)
    assert [(v.country, v.case_number) for v in lines] == [("PL", None), ("LT", None), ("PL", 7)]
    # Without a term there is nothing to tell the same visa by.
    undated = VisaRecord(kind="schengen", country="PL")
    lines = visa_history([undated], [granted(valid_from=None, valid_to=None)], today=TODAY)
    assert [v.case_number for v in lines] == [None, 7]


def test_each_written_visa_is_taken_by_one_case_only():
    same = VisaRecord(
        kind="schengen", country="PL", valid_from=date(2026, 12, 10), valid_to=date(2027, 12, 9)
    )
    # Written twice, granted once: the second record is still hers.
    lines = visa_history([same, same], [granted()], today=TODAY)
    assert [v.case_number for v in lines] == [None, 7]
    # Written once, in two cases (a duplicate case): one line for each case.
    lines = visa_history([same], [granted(7), granted(8)], today=TODAY)
    assert [v.case_number for v in lines] == [7, 8]
