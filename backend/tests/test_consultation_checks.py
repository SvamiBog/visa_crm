"""Questionnaire checks (docs/clients-module.md → «Анкета консультации»): pure rules."""

from datetime import date

from app.core.consultation_checks import Destination, Hint, check
from app.core.questionnaire import Questionnaire

TODAY = date(2026, 10, 16)
POLAND = Destination(code="PL", passport_max_age_years=10, passport_valid_after_months=3)
UK = Destination(code="GB", passport_max_age_years=None, passport_valid_after_months=None)
ITALY = Destination(code="IT", passport_max_age_years=10, passport_valid_after_months=3)


def codes(hints: list[Hint]) -> list[tuple[str, str | None]]:
    return [(h.code, h.country) for h in hints]


def test_nothing_to_say_about_a_clean_questionnaire():
    q = Questionnaire(passport_issued_on=date(2022, 1, 10), passport_expires_on=date(2032, 1, 9))
    assert check(q, [POLAND], trip_on=date(2026, 12, 1), today=TODAY) == []


def test_passport_must_outlive_the_trip_by_the_country_margin():
    # Valid until 15.02.2027; trip on 01.12.2026 + 3 months = 01.03.2027 → too soon.
    q = Questionnaire(passport_issued_on=date(2017, 2, 16), passport_expires_on=date(2027, 2, 15))
    assert codes(check(q, [POLAND], trip_on=date(2026, 12, 1), today=TODAY)) == [
        ("passport_expires_too_soon", "PL")
    ]
    # The UK has no such margin in the reference: no warning.
    assert check(q, [UK], trip_on=date(2026, 12, 1), today=TODAY) == []


def test_without_a_trip_date_today_is_used():
    q = Questionnaire(passport_issued_on=date(2017, 2, 16), passport_expires_on=date(2027, 1, 1))
    assert codes(check(q, [POLAND], trip_on=None, today=TODAY)) == [
        ("passport_expires_too_soon", "PL")
    ]


def test_passport_issued_more_than_ten_years_before_the_trip():
    q = Questionnaire(passport_issued_on=date(2016, 11, 30), passport_expires_on=date(2030, 1, 1))
    assert codes(check(q, [POLAND], trip_on=date(2026, 12, 1), today=TODAY)) == [
        ("passport_too_old", "PL")
    ]


def test_expired_passport_is_reported_once_for_all_countries():
    q = Questionnaire(passport_issued_on=date(2015, 1, 1), passport_expires_on=date(2026, 9, 1))
    assert codes(check(q, [POLAND, UK], trip_on=None, today=TODAY)) == [("passport_expired", None)]


def test_refusal_in_the_same_country():
    q = Questionnaire(refusals=[{"country": "PL", "year": 2024, "reason": "цель поездки"}])
    assert codes(check(q, [POLAND, UK], trip_on=None, today=TODAY)) == [("refusal_before", "PL")]


def test_deportation_is_a_risk_whatever_the_country():
    q = Questionnaire(deportation=True)
    assert codes(check(q, [UK], trip_on=None, today=TODAY)) == [("deportation", None)]


def test_invitations_from_relatives_and_humanitarian_trips():
    q = Questionnaire(relatives_abroad=["IT"], humanitarian_trips=["IT", "ES"])
    assert codes(check(q, [ITALY, POLAND], trip_on=None, today=TODAY)) == [
        ("invitation_relatives", "IT"),
        ("invitation_humanitarian", "IT"),
    ]


def test_unknown_passport_dates_are_not_guessed():
    assert check(Questionnaire(), [POLAND], trip_on=None, today=TODAY) == []
