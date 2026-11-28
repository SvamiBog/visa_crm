"""When the passport's data goes after the decision (backlog 2.8): the rule alone."""

from datetime import UTC, date, datetime, timedelta

from app.core.passport_purge import (
    Decision,
    brings_back,
    filled_fields,
    notice_on,
    postponed_until,
    purge_on,
    switched_on,
    with_grace,
)

D = date(2026, 12, 1)


def on(day: int) -> date:
    return date(2026, 12, day)


def test_a_week_after_the_decision_entered_long_ago():
    assert purge_on([Decision(on(1), on(1))], open_case=False, kept_until=None) == on(8)


def test_without_a_date_of_decision_the_week_counts_from_the_day_it_was_entered():
    assert purge_on([Decision(None, on(3))], open_case=False, kept_until=None) == on(10)


def test_a_result_entered_late_still_waits_two_days_after_it_is_entered():
    # Decided on the 1st, entered on the 20th: not that very night, but on the 22nd.
    assert purge_on([Decision(on(1), on(20))], open_case=False, kept_until=None) == on(22)
    # Entered on the 6th: the week from the decision ends later, on the 8th.
    assert purge_on([Decision(on(1), on(6))], open_case=False, kept_until=None) == on(8)


def test_the_latest_decision_of_the_client_counts():
    decisions = [Decision(on(1), on(1)), Decision(on(10), on(10)), Decision(on(5), on(5))]
    assert purge_on(decisions, open_case=False, kept_until=None) == on(17)


def test_nothing_goes_while_another_case_is_in_work():
    assert purge_on([Decision(on(1), on(1))], open_case=True, kept_until=None) is None


def test_nothing_goes_without_a_decision():
    assert purge_on([], open_case=False, kept_until=None) is None


def test_put_off_or_the_first_run_keeps_it_until_that_day():
    assert purge_on([Decision(on(1), on(1))], open_case=False, kept_until=on(20)) == on(20)
    # A later decision after the postponement counts on its own.
    assert purge_on([Decision(on(25), on(25))], open_case=False, kept_until=on(20)) == date(
        2027, 1, 1
    )


def test_the_notice_comes_two_days_before():
    assert notice_on(on(10), None) == on(8)


def test_the_first_run_lists_from_the_release_day():
    assert notice_on(on(15), on(1)) == on(1)
    # A notice day later than two days before changes nothing.
    assert notice_on(on(10), on(9)) == on(8)


def test_postponed_thirty_days_from_the_day_due_or_from_today():
    assert postponed_until(on(10), on(8)) == date(2027, 1, 9)
    assert postponed_until(on(10), on(12)) == date(2027, 1, 11)
    # Again and again, for an appeal: each time 30 more days.
    assert postponed_until(date(2027, 1, 9), on(30)) == date(2027, 2, 8)


def test_only_the_filled_fields_of_the_passport_are_named():
    questionnaire = {
        "birth_place": "Минск",
        "passport_number": "MP1234567",
        "personal_number": None,
        "nationality": "BY",
        "sex": "F",
    }
    assert filled_fields(questionnaire) == ["passport_number", "birth_place"]
    assert filled_fields({"nationality": "BY"}) == []
    assert filled_fields({}) == []


# --- Production: off until PASSPORT_PURGE_FROM ------------------------------------------


def test_the_stands_remove_always_and_production_only_from_the_day_set():
    for env in ("local", "test", "staging"):
        assert switched_on(env, None, on(1))
    assert not switched_on("production", None, on(1))
    assert not switched_on("production", on(2), on(1))
    assert switched_on("production", on(2), on(2))
    assert switched_on("production", on(2), on(3))


def test_what_is_due_before_the_switch_waits_fourteen_days_from_it_and_shows_at_once():
    # Due long ago, or kept by the release's first run until a day now past.
    assert with_grace(on(5), None, on(10)) == (on(24), on(10))
    assert with_grace(on(5), on(1), on(10)) == (on(24), on(1))
    # Due within the fourteen days: also from the switch day + 14, shown from the switch.
    assert with_grace(on(20), None, on(10)) == (on(24), on(10))


def test_what_is_due_after_the_grace_keeps_its_own_day():
    assert with_grace(on(24), None, on(10)) == (on(24), None)
    assert with_grace(on(30), on(1), on(10)) == (on(30), on(1))
    # No switch day (the stands): nothing changes.
    assert with_grace(on(5), on(3), None) == (on(5), on(3))


# --- A form opened before the removal (backlog A11) ----------------------------------

OPENED = datetime(2026, 12, 1, 10, 0, tzinfo=UTC)
PURGED = {"passport_number": None, "birth_place": None, "nationality": "BY"}
OLD_FORM = {"passport_number": "MP1234567", "birth_place": "Минск", "nationality": "BY"}


def test_a_form_opened_before_the_card_changed_does_not_fill_what_is_empty_now():
    later = OPENED + timedelta(seconds=1)
    assert brings_back(PURGED, OLD_FORM, OPENED, later)
    # One field is enough.
    assert brings_back(PURGED, {"birth_place": "Минск"}, OPENED, later)
    # The version is a mark, not a time: the daily run stamps the start of its transaction,
    # so a card saved during the run gets older again after the removal.
    assert brings_back(PURGED, OLD_FORM, later, OPENED)


def test_a_fresh_form_a_form_without_its_version_or_fields_kept_are_let_through():
    later = OPENED + timedelta(seconds=1)
    # Opened from the card as it is now.
    assert not brings_back(PURGED, OLD_FORM, later, later)
    # No version: an import or a passport read from a photo, new data on purpose.
    assert not brings_back(PURGED, OLD_FORM, None, later)
    # The fields are filled now: the form changes them, nothing removed comes back.
    assert not brings_back(OLD_FORM, {**OLD_FORM, "passport_number": "MP7654321"}, OPENED, later)
    # Kept empty, or a field the removal does not touch.
    assert not brings_back(PURGED, {"passport_number": None, "nationality": "PL"}, OPENED, later)
