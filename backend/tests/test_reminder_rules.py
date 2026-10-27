"""When an in-CRM reminder comes up (docs/clients-module.md → «Напоминания»).

A reminder has trigger days («3 days before and the day before»): it shows from the
latest trigger that has come until the event is over. «Готово» hides that occurrence
only, so the next trigger brings it back.
"""

from datetime import date
from decimal import Decimal as D

from app.core import reminders as r

S = r.Settings()
TODAY = date(2026, 10, 16)


def days(n: int) -> date:
    return date.fromordinal(TODAY.toordinal() + n)


def test_the_latest_trigger_that_came_until_the_event_is_over():
    assert r.occurrence([days(-3), days(-1)], TODAY, until=days(1)) == days(-1)
    assert r.occurrence([days(2)], TODAY) is None  # not yet
    assert r.occurrence([days(-5)], TODAY, until=days(-1)) is None  # over


def test_submission_three_days_before_and_the_day_before():
    sub = days(3)
    assert r.submission(sub, TODAY, S) == TODAY  # the 3-day trigger is today
    assert r.submission(days(1), TODAY, S) == TODAY  # tomorrow: the «day before» trigger
    assert r.submission(days(2), TODAY, S) == days(-1)  # still the 3-day occurrence
    assert r.submission(days(4), TODAY, S) is None
    assert r.submission(TODAY, TODAY, S) == days(-1)  # the day itself still shows
    assert r.submission(days(-1), TODAY, S) is None


def test_an_unpaid_balance_before_the_submission():
    assert r.balance_due(days(2), D("150.00"), TODAY, S) == days(-1)
    assert r.balance_due(days(2), D("0.00"), TODAY, S) is None
    assert r.balance_due(days(2), None, TODAY, S) is None  # no price yet
    assert r.balance_due(days(5), D("150.00"), TODAY, S) is None


def test_the_application_window_deadline_stays_while_the_attempt_is_open():
    assert r.application_deadline(days(5), TODAY, S) == TODAY
    assert r.application_deadline(days(6), TODAY, S) is None
    assert r.application_deadline(TODAY, TODAY, S) == TODAY  # the deadline day
    assert r.application_deadline(days(-2), TODAY, S) == days(-2)  # overdue: still open


def test_booking_cancellation_three_days_and_one_day_before():
    assert r.booking_deadline(days(3), TODAY, S) == TODAY
    assert r.booking_deadline(days(1), TODAY, S) == TODAY
    assert r.booking_deadline(TODAY, TODAY, S) == days(-1)
    assert r.booking_deadline(days(-1), TODAY, S) is None


def test_thinking_clients_after_one_and_three_months():
    assert r.thinking(date(2026, 9, 16), TODAY, S) == TODAY
    assert r.thinking(date(2026, 9, 20), TODAY, S) is None
    assert r.thinking(date(2026, 8, 1), TODAY, S) == date(2026, 9, 1)
    assert r.thinking(date(2026, 7, 10), TODAY, S) == date(2026, 10, 10)  # 3 months
    assert r.thinking(date(2026, 1, 31), date(2026, 2, 28), S) == date(2026, 2, 28)
    # A month after the last reminder the client is no longer «thinking» about this.
    assert r.thinking(date(2026, 6, 16), TODAY, S) == date(2026, 9, 16)
    assert r.thinking(date(2026, 6, 15), TODAY, S) is None
    assert r.thinking(date(2025, 3, 14), TODAY, S) is None


def test_visa_expiry_two_months_before():
    assert r.visa_expires(date(2026, 12, 16), TODAY, S) == TODAY
    assert r.visa_expires(date(2026, 12, 17), TODAY, S) is None
    assert r.visa_expires(days(-1), TODAY, S) is None


def test_appeal_right_away_and_three_days_before_the_end():
    decided = days(-10)
    assert r.appeal(decided, days(20), TODAY, S) == decided
    assert r.appeal(decided, days(2), TODAY, S) == days(-1)
    assert r.appeal(decided, days(-1), TODAY, S) is None


def test_birthdays_on_the_day_and_leap_years():
    assert r.birthday(date(1990, 10, 16), TODAY) == TODAY
    assert r.birthday(date(1990, 10, 17), TODAY) is None
    assert r.birthday(date(2000, 2, 29), date(2027, 2, 28)) == date(2027, 2, 28)
    assert r.birthday(date(2000, 2, 29), date(2028, 2, 28)) is None  # leap year: the 29th


def test_keys_name_the_occurrence_without_client_data():
    subject = "8c84cfc8-4d1e-4d1b-b024-4a00a64da3a1"
    key = r.key(r.ReminderKind.SUBMISSION, subject, days(-1))
    assert key == f"submission:{subject}:2026-10-15"
    assert r.parse_key(key) == (r.ReminderKind.SUBMISSION, subject, days(-1))
    assert r.parse_key("submission:not-a-uuid:2026-10-15") is None
    assert r.parse_key("unknown:" + subject + ":2026-10-15") is None


def test_settings_change_the_windows():
    wide = r.Settings(submission_days=7, thinking_months=(2,))
    assert r.submission(days(7), TODAY, wide) == TODAY
    assert r.thinking(date(2026, 9, 16), TODAY, wide) is None


# What the specialist now reminds clients of by hand (backlog 1.19, question 4).


def test_a_consultation_the_day_before_and_on_the_day():
    assert r.consultation(days(1), TODAY, S) == TODAY  # tomorrow: the day-before trigger
    assert r.consultation(TODAY, TODAY, S) == TODAY  # today: the day itself
    assert r.consultation(days(2), TODAY, S) is None  # not yet
    assert r.consultation(days(-1), TODAY, S) is None  # over


def test_the_agreed_follow_up_on_its_day_and_for_a_month():
    assert r.follow_up(TODAY, TODAY) == TODAY
    assert r.follow_up(days(1), TODAY) is None
    assert r.follow_up(date(2026, 9, 16), TODAY) == date(2026, 9, 16)  # a month on: still there
    assert r.follow_up(date(2026, 9, 15), TODAY) is None  # longer ago: gone


def test_an_unsigned_contract_three_days_after_the_case_opened_and_for_a_month():
    assert r.contract_unsigned(days(-3), TODAY, S) == TODAY
    assert r.contract_unsigned(days(-2), TODAY, S) is None  # not yet
    # Opened 13.09: the reminder came on 16.09 and stays a month, to 16.10.
    assert r.contract_unsigned(date(2026, 9, 13), TODAY, S) == date(2026, 9, 16)
    # Opened earlier (an old case from the tables): no reminder.
    assert r.contract_unsigned(date(2026, 9, 12), TODAY, S) is None


def test_the_questionnaire_the_day_before_it_is_due_and_after_it_until_received():
    assert r.questionnaire(days(1), TODAY, S) == TODAY
    assert r.questionnaire(TODAY, TODAY, S) == TODAY
    assert r.questionnaire(days(2), TODAY, S) is None
    assert r.questionnaire(days(-5), TODAY, S) == days(-5)  # overdue: stays


def test_the_balance_right_after_the_queue_until_the_reminder_before_the_submission():
    owed = D("300.00")
    assert r.balance_queued(TODAY, owed, None, TODAY, S) == TODAY
    assert r.balance_queued(days(-60), owed, None, TODAY, S) == days(-60)  # no date yet: stays
    assert r.balance_queued(TODAY, D("0.00"), None, TODAY, S) is None  # paid
    assert r.balance_queued(TODAY, D("-20.00"), None, TODAY, S) is None  # paid more
    assert r.balance_queued(TODAY, None, None, TODAY, S) is None  # no price yet
    # The submission is near: the reminder 3 days before it takes over, no two at once.
    assert r.balance_queued(days(-30), owed, days(4), TODAY, S) == days(-30)
    assert r.balance_queued(days(-30), owed, days(3), TODAY, S) is None
    assert r.balance_due(days(3), owed, TODAY, S) == TODAY


def test_settings_change_the_new_windows():
    wide = r.Settings(consultation_days=2, contract_days=7, questionnaire_days=3)
    assert r.consultation(days(2), TODAY, wide) == TODAY
    assert r.contract_unsigned(days(-3), TODAY, wide) is None
    assert r.contract_unsigned(days(-7), TODAY, wide) == TODAY
    assert r.questionnaire(days(3), TODAY, wide) == TODAY


# The package check (backlog 2.3): documents not collected before the submission.


def test_an_incomplete_package_a_week_and_the_day_before_the_submission():
    assert S.package_days == 7
    assert r.package(days(7), False, TODAY, S) == TODAY  # the week trigger is today
    assert r.package(days(8), False, TODAY, S) is None  # not yet
    assert r.package(days(3), False, TODAY, S) == days(-4)  # still the week occurrence
    assert r.package(days(1), False, TODAY, S) == TODAY  # tomorrow: the day-before trigger
    assert r.package(TODAY, False, TODAY, S) == days(-1)  # the day itself still shows
    assert r.package(days(-1), False, TODAY, S) is None  # submitted: over


def test_a_ready_package_gives_no_reminder():
    assert r.package(days(7), True, TODAY, S) is None
    assert r.package(days(1), True, TODAY, S) is None


def test_settings_change_the_package_window():
    wide = r.Settings(package_days=14)
    assert r.package(days(14), False, TODAY, wide) == TODAY
    assert r.package(days(7), False, TODAY, wide) == days(-7)
