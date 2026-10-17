"""The Schengen 90/180 rule (backlog 2.6): app/core/stay.py."""

import random
from datetime import date, timedelta

from app.core.stay import (
    MAX_DAYS,
    Days,
    Period,
    Trip,
    check,
    earliest_entry,
    overstays,
    stay_until,
    summary,
    trip_results,
)

TODAY = date(2026, 10, 17)


def days_of(*trips: Trip, today: date = TODAY) -> Days:
    return Days.of(trips, today)


def test_entry_and_exit_days_both_count():
    days = days_of(Trip(date(2026, 5, 1), date(2026, 5, 10)))
    assert days.used_on(date(2026, 5, 10)) == 10
    assert days.used_on(date(2026, 5, 1)) == 1
    assert days.used_on(date(2026, 4, 30)) == 0


def test_the_period_is_the_day_and_the_179_days_before_it():
    days = days_of(Trip(date(2026, 1, 1), date(2026, 1, 1)))
    # 1 January is the 180th day back from 29 June and drops out on 30 June.
    assert days.used_on(date(2026, 6, 29)) == 1
    assert days.used_on(date(2026, 6, 30)) == 0


def test_ninety_days_fit_and_the_91st_is_an_overstay():
    # 1 January – 31 March 2026 is exactly 90 days.
    days = days_of(Trip(date(2026, 1, 1), date(2026, 3, 31)))
    assert days.used_on(date(2026, 3, 31)) == MAX_DAYS
    assert overstays(days) == []
    assert stay_until(days, date(2026, 1, 1)) == date(2026, 3, 31)

    longer = days_of(Trip(date(2026, 1, 1), date(2026, 4, 2)))
    assert overstays(longer) == [Period(date(2026, 4, 1), date(2026, 4, 2))]


def test_after_ninety_days_the_next_entry_waits_for_days_to_drop_out():
    days = days_of(Trip(date(2026, 1, 1), date(2026, 3, 31)))
    assert stay_until(days, date(2026, 6, 29)) is None  # still 90 in the window
    # From 30 June one day drops out every day, so the stay can go on for 90 days.
    assert stay_until(days, date(2026, 6, 30)) == date(2026, 9, 27)
    assert earliest_entry(days, 1, date(2026, 4, 1)) == date(2026, 6, 30)
    assert earliest_entry(days, MAX_DAYS, date(2026, 4, 1)) == date(2026, 6, 30)


def test_days_left_are_not_simply_ninety_minus_used():
    # 60 days used early in the window: they drop out during the next stay,
    # so a stay from 1 June can still last the full 90 days, not 30.
    days = days_of(Trip(date(2026, 1, 1), date(2026, 3, 1)))
    assert days.used_on(date(2026, 5, 31)) == 60
    assert stay_until(days, date(2026, 6, 1)) == date(2026, 8, 29)
    # Entering earlier, while all 60 are still in the window, gives 30 days.
    assert stay_until(days, date(2026, 3, 10)) == date(2026, 4, 8)


def test_overlapping_trips_are_not_counted_twice():
    days = days_of(
        Trip(date(2026, 5, 1), date(2026, 5, 10)), Trip(date(2026, 5, 5), date(2026, 5, 12))
    )
    assert days.used_on(date(2026, 5, 31)) == 12


def test_only_schengen_days_under_a_short_stay_count():
    # A trip outside the area or under a long-stay visa (counts_from None) does not count.
    days = days_of(
        Trip(date(2026, 5, 1), date(2026, 5, 10), counts_from=None),
        Trip(date(2026, 6, 1), date(2026, 6, 3)),
    )
    assert days.used_on(date(2026, 6, 30)) == 3
    # A country counts from the day it joined: Bulgaria from 31 March 2024.
    joined = days_of(
        Trip(date(2024, 3, 25), date(2024, 4, 3), counts_from=date(2024, 3, 31)),
        today=date(2024, 5, 1),
    )
    assert joined.used_on(date(2024, 4, 30)) == 4


def test_a_stay_still_going_on_counts_up_to_today_and_shows_the_last_day():
    days = days_of(Trip(date(2026, 9, 1)))  # still there
    assert days.used_on(TODAY) == 47
    result = summary(days, TODAY)
    assert result.in_area and result.used == 47
    # 47 days by 17 October: 43 more, up to 29 November.
    assert result.stay_until == date(2026, 11, 29)


def test_summary_for_someone_outside_the_area():
    days = days_of(Trip(date(2026, 8, 1), date(2026, 8, 30)))
    result = summary(days, TODAY)
    assert not result.in_area
    assert result.used == 30
    # Entering today: 60 days left while August is in the window, and August drops out
    # from late January, after the stay ends.
    assert result.stay_until == date(2026, 12, 15)
    # A full 90 days once the last of August is more than 90 days back.
    assert result.full_stay_from == date(2026, 11, 29)
    assert stay_until(days, date(2026, 11, 29)) == date(2027, 2, 26)
    assert stay_until(days, date(2026, 11, 28)) == date(2027, 1, 26)
    assert result.overstays == []


def test_no_trips_mean_the_full_ninety_days_from_today():
    result = summary(days_of(), TODAY)
    assert (result.used, result.in_area) == (0, False)
    assert result.next_entry == TODAY
    assert result.stay_until == TODAY + timedelta(days=MAX_DAYS - 1)
    assert result.full_stay_from == TODAY


def test_each_trip_shows_its_days_and_the_count_on_its_last_day():
    trips = [
        Trip(date(2026, 1, 1), date(2026, 2, 14)),  # 45 days
        Trip(date(2026, 3, 1), date(2026, 4, 20)),  # 51 days: overstay from 15 April
        Trip(date(2026, 5, 1), date(2026, 5, 2), counts_from=None),
    ]
    first, second, outside = trip_results(trips, days_of(*trips), TODAY)
    assert (first.days, first.used_at_end, first.overstay_from) == (45, 45, None)
    assert (second.days, second.used_at_end) == (51, 96)
    assert second.overstay_from == date(2026, 4, 15)
    assert (outside.days, outside.used_at_end, outside.overstay_from) == (0, None, None)


def test_planned_trip_that_fits():
    days = days_of(Trip(date(2026, 8, 1), date(2026, 8, 30)))
    result = check(days, date(2026, 11, 1), date(2026, 11, 20), TODAY)
    assert result.length == 20
    assert result.used_before == 30
    assert result.fits is True
    assert result.earliest_entry is None


def test_planned_trip_that_does_not_fit_shows_the_last_day_and_the_earliest_entry():
    days = days_of(Trip(date(2026, 8, 1), date(2026, 9, 29)))  # 60 days
    result = check(days, date(2026, 10, 20), date(2026, 12, 31), TODAY)
    assert result.length == 73
    assert result.used_before == 60
    assert result.fits is False
    assert result.stay_until == date(2026, 11, 18)  # 30 days
    # The earliest day from today a 73-day trip fits.
    assert result.earliest_entry is not None
    assert result.earliest_entry > TODAY
    assert stay_until(days, result.earliest_entry) >= result.earliest_entry + timedelta(72)
    before = result.earliest_entry - timedelta(days=1)
    assert stay_until(days, before) < before + timedelta(72)


def test_planned_trip_without_an_exit_date_gives_the_last_day():
    days = days_of(Trip(date(2026, 8, 1), date(2026, 8, 30)))
    result = check(days, date(2026, 11, 1), None, TODAY)
    assert result.length is None and result.fits is None
    assert result.stay_until == stay_until(days, date(2026, 11, 1))


def test_a_trip_longer_than_ninety_days_never_fits():
    result = check(days_of(), date(2026, 11, 1), date(2027, 2, 1), TODAY)
    assert result.fits is False
    assert result.earliest_entry is None


def test_entry_is_impossible_while_the_window_is_full():
    days = days_of(Trip(date(2026, 7, 20), date(2026, 10, 17)))  # 90 days up to today
    result = check(days, date(2026, 10, 18), None, TODAY)
    assert result.stay_until is None
    # Still there today, so the next entry is from tomorrow on:
    # 20 July drops out of the period on 16 January.
    assert result.earliest_entry == date(2027, 1, 16)
    now = summary(days, TODAY)
    assert now.in_area and now.stay_until == TODAY
    assert now.next_entry == TODAY


# --- Against a plain day-by-day count --------------------------------------------------


def brute_days(trips: list[Trip], today: date) -> set[date]:
    found = set()
    for trip in trips:
        if trip.counts_from is None:
            continue
        day, last = trip.entered_on, trip.left_on or today
        while day <= last:
            if day >= trip.counts_from:
                found.add(day)
            day += timedelta(days=1)
    return found


def brute_used(days: set[date], day: date) -> int:
    return sum(day - timedelta(days=k) in days for k in range(180))


def brute_stay_until(days: set[date], entered_on: date) -> date | None:
    stay, last, day = set(days), None, entered_on
    while day - entered_on < timedelta(days=200):
        stay.add(day)
        if brute_used(stay, day) > MAX_DAYS:
            break
        last, day = day, day + timedelta(days=1)
    return last


def random_trips(rng: random.Random) -> list[Trip]:
    trips, day = [], TODAY - timedelta(days=rng.randrange(200, 400))
    for _ in range(rng.randrange(0, 7)):
        day += timedelta(days=rng.randrange(1, 60))
        length = rng.randrange(1, 70)
        counts = None if rng.random() < 0.15 else date.min
        trips.append(Trip(day, day + timedelta(days=length - 1), counts_from=counts))
        day += timedelta(days=length)
    return trips


def test_matches_a_plain_day_by_day_count():
    rng = random.Random(90180)
    for _ in range(20):
        trips = random_trips(rng)
        days, plain = days_of(*trips), brute_days(trips, TODAY)
        start = TODAY - timedelta(days=400)
        for offset in range(0, 500, 5):
            day = start + timedelta(days=offset)
            assert days.used_on(day) == brute_used(plain, day)
            if offset % 15 == 0:
                assert stay_until(days, day) == brute_stay_until(plain, day)
        over = [d for d in sorted(plain) if brute_used(plain, d) > MAX_DAYS]
        assert sum((p.last - p.first).days + 1 for p in overstays(days)) == len(over)
        length = rng.randrange(1, MAX_DAYS + 1)
        entry = earliest_entry(days, length, TODAY)
        assert entry is not None
        until = brute_stay_until(plain, entry)
        assert until is not None and (until - entry).days + 1 >= length
        previous = brute_stay_until(plain, entry - timedelta(days=1))
        if entry > TODAY:
            assert previous is None or (previous - entry).days + 2 < length
