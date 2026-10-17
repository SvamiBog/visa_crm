"""The Schengen 90/180 rule (backlog 2.6; docs/decisions.md → «Калькулятор 90/180»).

A short stay in the Schengen area may not exceed 90 days in any 180-day period
(Schengen Borders Code, Regulation (EU) 2016/399, art. 6(1)). For each day of a stay the
period is that day and the 179 days before it; the days of entry and exit both count.

A day counts only in a country of the area from the day it joined (the countries
reference: `schengen_since`) and not under a long-stay visa (D) or a residence permit,
which have their own rules. A trip still going on counts up to today.

How long someone may stay is not «90 minus days used»: old days drop out of the period
while the stay goes on, so each day is checked on its own.
"""

from bisect import bisect_left, bisect_right
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date

WINDOW_DAYS = 180
MAX_DAYS = 90


@dataclass(frozen=True)
class Trip:
    entered_on: date
    # None: still there.
    left_on: date | None = None
    # The first day the trip counts: when its country joined the area. None: never
    # (outside the area, or under a long-stay visa or a residence permit).
    counts_from: date | None = date.min


@dataclass(frozen=True)
class Period:
    first: date
    last: date


class Days:
    """Days spent in the area, kept sorted for fast counts over any period."""

    def __init__(self, ordinals: list[int]) -> None:
        self._days = ordinals

    @classmethod
    def of(cls, trips: Iterable[Trip], today: date) -> "Days":
        spans = sorted(span for trip in trips if (span := _span(trip, today)) is not None)
        merged: list[list[int]] = []
        for first, last in spans:
            if merged and first <= merged[-1][1] + 1:
                merged[-1][1] = max(merged[-1][1], last)
            else:
                merged.append([first, last])
        return cls([day for first, last in merged for day in range(first, last + 1)])

    @property
    def last(self) -> int | None:
        return self._days[-1] if self._days else None

    def __iter__(self):
        return iter(self._days)

    def count(self, first: int, last: int) -> int:
        """Days spent from `first` to `last`, both included (ordinals)."""
        if last < first:
            return 0
        return bisect_right(self._days, last) - bisect_left(self._days, first)

    def used_on(self, day: date) -> int:
        """Days spent in the 180-day period ending on `day`."""
        end = day.toordinal()
        return self.count(end - WINDOW_DAYS + 1, end)


def _span(trip: Trip, today: date) -> tuple[int, int] | None:
    if trip.counts_from is None:
        return None
    first = max(trip.entered_on, trip.counts_from)
    last = trip.left_on if trip.left_on is not None else today
    return (first.toordinal(), last.toordinal()) if first <= last else None


def _used_with_stay(days: Days, start: int, day: int) -> int:
    """Days in the period ending on `day` if the person stays from `start` to `day`."""
    window_start = day - WINDOW_DAYS + 1
    stay_start = max(start, window_start)
    stay = (day - stay_start + 1) - days.count(stay_start, day)
    return days.count(window_start, day) + stay


def stay_until(days: Days, entered_on: date) -> date | None:
    """The last day someone entering on `entered_on` may stay (or the day they must
    leave by, if they are there already). None: entering that day already breaks the rule.
    A stay cannot go on for more than 90 days, so at most 90 days are checked."""
    start = entered_on.toordinal()
    last = None
    for day in range(start, start + MAX_DAYS + 1):
        if _used_with_stay(days, start, day) > MAX_DAYS:
            break
        last = day
    return None if last is None else date.fromordinal(last)


def earliest_entry(days: Days, length: int, start: date) -> date | None:
    """The first day from `start` on which a stay of `length` days fits. None for a stay
    longer than 90 days. Once the last day spent is 180 days back, any stay fits."""
    if length > MAX_DAYS:
        return None
    first = start.toordinal()
    bound = max(first, (days.last or first) + WINDOW_DAYS)
    for day in range(first, bound + 1):
        until = stay_until(days, date.fromordinal(day))
        if until is not None and until.toordinal() - day + 1 >= length:
            return date.fromordinal(day)
    return None  # unreachable: by `bound` the period is empty


def overstays(days: Days) -> list[Period]:
    """Runs of days on which more than 90 days were (or will be) spent in the period."""
    periods: list[list[int]] = []
    for day in days:
        if days.count(day - WINDOW_DAYS + 1, day) <= MAX_DAYS:
            continue
        if periods and periods[-1][1] == day - 1:
            periods[-1][1] = day
        else:
            periods.append([day, day])
    return [Period(date.fromordinal(a), date.fromordinal(b)) for a, b in periods]


@dataclass(frozen=True)
class TripResult:
    # Days of this trip that count.
    days: int
    # Days spent in the period ending on its last day (the exit, or today while still
    # there): what the border guard sees. None for a trip that does not count.
    used_at_end: int | None
    # The first day of the trip over the limit.
    overstay_from: date | None


def trip_results(trips: Iterable[Trip], days: Days, today: date) -> list[TripResult]:
    results = []
    for trip in trips:
        span = _span(trip, today)
        if span is None:
            results.append(TripResult(days=0, used_at_end=None, overstay_from=None))
            continue
        first, last = span
        over = next(
            (d for d in range(first, last + 1) if days.count(d - WINDOW_DAYS + 1, d) > MAX_DAYS),
            None,
        )
        results.append(
            TripResult(
                days=last - first + 1,
                used_at_end=days.count(last - WINDOW_DAYS + 1, last),
                overstay_from=None if over is None else date.fromordinal(over),
            )
        )
    return results


@dataclass(frozen=True)
class Summary:
    # Days spent in the 180 days up to today.
    used: int
    # In the area today (a trip is going on).
    in_area: bool
    # In the area: the day to leave by. Outside: the last day of a stay entering today.
    # None: entering today breaks the rule.
    stay_until: date | None
    # The first day from today any stay fits: today, unless the period is full.
    next_entry: date
    # The first day from today a full 90-day stay fits.
    full_stay_from: date
    overstays: list[Period] = field(default_factory=list)


def summary(days: Days, today: date) -> Summary:
    entry, full = earliest_entry(days, 1, today), earliest_entry(days, MAX_DAYS, today)
    assert entry is not None and full is not None
    return Summary(
        used=days.used_on(today),
        in_area=days.count(today.toordinal(), today.toordinal()) == 1,
        stay_until=stay_until(days, today),
        next_entry=entry,
        full_stay_from=full,
        overstays=overstays(days),
    )


@dataclass(frozen=True)
class Check:
    """A planned trip checked against the days already spent and planned."""

    # Days of the planned trip; None without an exit date.
    length: int | None
    # Days spent in the 179 days before the entry.
    used_before: int
    # The last day of a stay entering that day; None: the entry itself breaks the rule.
    stay_until: date | None
    # None without an exit date.
    fits: bool | None
    # When the trip (or, without an exit date or entry, any stay) does not fit: the first
    # day from today (from tomorrow, if in the area today) it would. None for a trip
    # longer than 90 days.
    earliest_entry: date | None


def check(days: Days, entered_on: date, left_on: date | None, today: date) -> Check:
    entry = entered_on.toordinal()
    until = stay_until(days, entered_on)
    length = None if left_on is None else (left_on - entered_on).days + 1
    fits = None if left_on is None else until is not None and left_on <= until
    earliest = None
    if fits is False or until is None:
        # Someone in the area today can enter again from tomorrow at the earliest.
        now = today.toordinal()
        start = today if days.count(now, now) == 0 else date.fromordinal(now + 1)
        earliest = earliest_entry(days, length or 1, start)
    return Check(
        length=length,
        used_before=days.count(entry - WINDOW_DAYS + 1, entry - 1),
        stay_until=until,
        fits=fits,
        earliest_entry=earliest,
    )
