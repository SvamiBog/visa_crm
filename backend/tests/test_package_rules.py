"""The package check of a case (backlog 2.3): the status of a checklist item at the
submission date and when the package is ready (docs/decisions.md → «Проверка пакета»)."""

from datetime import date, timedelta

from app.core.package import ItemStatus, item_status, reference_day, summarize
from app.models import PackageItemState

SUBMISSION = date(2026, 11, 20)
PRESENT = PackageItemState.PRESENT


def days_before(days: int) -> date:
    return SUBMISSION - timedelta(days=days)


def test_an_unmarked_or_absent_document_is_missing():
    assert item_status(None, None, None, SUBMISSION) == ItemStatus.MISSING
    assert item_status(PackageItemState.MISSING, None, 30, SUBMISSION) == ItemStatus.MISSING


def test_a_document_marked_not_needed_needs_nothing_else():
    assert item_status(PackageItemState.NOT_NEEDED, None, 30, SUBMISSION) == ItemStatus.NOT_NEEDED


def test_a_document_without_a_freshness_limit_is_fine_with_or_without_its_date():
    assert item_status(PRESENT, None, None, SUBMISSION) == ItemStatus.OK
    assert item_status(PRESENT, days_before(4000), None, SUBMISSION) == ItemStatus.OK


def test_a_document_with_a_freshness_limit_needs_its_date():
    assert item_status(PRESENT, None, 30, SUBMISSION) == ItemStatus.NO_DATE


def test_freshness_is_counted_in_days_to_the_submission():
    # «Not older than 30 days at submission»: issued 30 days before is still fine.
    assert item_status(PRESENT, days_before(30), 30, SUBMISSION) == ItemStatus.OK
    assert item_status(PRESENT, days_before(31), 30, SUBMISSION) == ItemStatus.EXPIRED
    assert item_status(PRESENT, days_before(40), 30, SUBMISSION) == ItemStatus.EXPIRED
    assert item_status(PRESENT, SUBMISSION, 30, SUBMISSION) == ItemStatus.OK
    assert item_status(PRESENT, days_before(1), 0, SUBMISSION) == ItemStatus.EXPIRED


def test_freshness_goes_by_the_submission_date_and_until_there_is_one_by_today():
    today = date(2026, 10, 25)
    assert reference_day(SUBMISSION, today) == SUBMISSION
    assert reference_day(None, today) == today
    # The same certificate: fine today, too old by the submission four weeks later.
    issued = today - timedelta(days=10)
    assert item_status(PRESENT, issued, 30, reference_day(None, today)) == ItemStatus.OK
    assert item_status(PRESENT, issued, 30, reference_day(SUBMISSION, today)) == (
        ItemStatus.EXPIRED
    )


def test_the_package_is_ready_when_no_required_document_is_missing_undated_or_expired():
    ready = summarize(
        [
            (True, ItemStatus.OK),
            (True, ItemStatus.NOT_NEEDED),
            # «По ситуации» items are shown but do not hold the package back.
            (False, ItemStatus.MISSING),
            (False, ItemStatus.EXPIRED),
        ]
    )
    assert ready.ready
    assert ready.counts == {
        ItemStatus.OK: 1,
        ItemStatus.MISSING: 1,
        ItemStatus.NO_DATE: 0,
        ItemStatus.EXPIRED: 1,
        ItemStatus.NOT_NEEDED: 1,
    }
    for blocking in (ItemStatus.MISSING, ItemStatus.NO_DATE, ItemStatus.EXPIRED):
        assert not summarize([(True, ItemStatus.OK), (True, blocking)]).ready


def test_an_empty_checklist_has_nothing_to_hold_the_package_back():
    empty = summarize([])
    assert empty.ready
    assert sum(empty.counts.values()) == 0
