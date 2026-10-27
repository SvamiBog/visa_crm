"""The package check of a case (backlog 2.3; docs/decisions.md → «Проверка пакета»).

Which checklist items a case gets is app/core/checklists.py. For each of them the
specialist marks the document as there, not there or not needed, and gives its date.
A document with a freshness limit («not older than 30 days at submission») is counted
to the submission date; until the case has one, to today. A document issued exactly
the limit's number of days before still counts.

The package is ready when no required item is missing, undated or expired. Items
«по ситуации» (not required) depend on the applicant: they are shown, but do not hold
the package back.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from enum import StrEnum

from app.models import PackageItemState


class ItemStatus(StrEnum):
    OK = "ok"
    MISSING = "missing"
    NO_DATE = "no_date"
    EXPIRED = "expired"
    NOT_NEEDED = "not_needed"


BLOCKING = frozenset({ItemStatus.MISSING, ItemStatus.NO_DATE, ItemStatus.EXPIRED})


@dataclass(frozen=True)
class Summary:
    ready: bool
    counts: dict[ItemStatus, int]
    # Required items holding the package back: what is left to collect.
    missing: int


def reference_day(submission_on: date | None, today: date) -> date:
    """The day freshness is counted to."""
    return submission_on or today


def item_status(
    state: PackageItemState | None,
    document_date: date | None,
    max_age_days: int | None,
    on: date,
) -> ItemStatus:
    """The status of one item on the reference day; an unmarked item is missing."""
    if state == PackageItemState.NOT_NEEDED:
        return ItemStatus.NOT_NEEDED
    if state != PackageItemState.PRESENT:
        return ItemStatus.MISSING
    if max_age_days is None:
        return ItemStatus.OK
    if document_date is None:
        return ItemStatus.NO_DATE
    if (on - document_date).days > max_age_days:
        return ItemStatus.EXPIRED
    return ItemStatus.OK


def summarize(items: Iterable[tuple[bool, ItemStatus]]) -> Summary:
    """Readiness and the number of items in each status; items are (required, status)."""
    counts = dict.fromkeys(ItemStatus, 0)
    missing = 0
    for required, status in items:
        counts[status] += 1
        if required and status in BLOCKING:
            missing += 1
    return Summary(ready=missing == 0, counts=counts, missing=missing)
