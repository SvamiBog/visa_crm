"""Rules of the client portal (backlog 2.9): the link, the one-time code, the client's
session and what the client sees for an item. No database."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from cryptography.exceptions import InvalidTag

from app.core import portal
from app.core.portal import ClientItemStatus, FileStatus
from app.models import CaseStage, PackageItemState, VisaOutcome

NOW = datetime(2026, 11, 24, 12, 0, tzinfo=UTC)
LINK = uuid.UUID("00000000-0000-0000-0000-000000000001")


# --- The link ------------------------------------------------------------------------


def test_a_link_token_carries_256_random_bits_and_only_its_hash_is_looked_up():
    tokens = {portal.new_link_token() for _ in range(50)}
    assert len(tokens) == 50
    token = tokens.pop()
    assert portal.is_link_token(token)
    assert len(token) == 43  # 32 bytes in URL-safe base64
    digest = portal.link_token_hash(token)
    assert len(digest) == 32 and token.encode() not in digest
    assert portal.link_token_hash(token) == digest


@pytest.mark.parametrize("token", ["", "short", "a" * 42, "a" * 44, "a" * 42 + "/", "é" * 43])
def test_anything_but_a_token_shape_is_not_a_link(token):
    assert not portal.is_link_token(token)


@pytest.mark.parametrize(
    ("stage", "outcome", "revoked", "open_"),
    [
        (CaseStage.WAITING, None, False, True),
        (CaseStage.DOCUMENTS, None, False, True),
        (CaseStage.SUBMISSION, None, False, True),
        # Handed over for review: the documents are collected, the page closes.
        (CaseStage.REVIEW, None, False, False),
        (CaseStage.RESULT, None, False, False),
        (CaseStage.SUBMISSION, VisaOutcome.NOT_SUBMITTED, False, False),
        (CaseStage.SUBMISSION, None, True, False),
    ],
)
def test_a_link_works_while_documents_are_collected_until_it_is_revoked(
    stage, outcome, revoked, open_
):
    assert portal.link_open(stage, outcome, NOW if revoked else None) is open_


# --- The code ------------------------------------------------------------------------


def test_a_code_is_six_ascii_digits():
    for _ in range(50):
        code = portal.new_code()
        assert len(code) == 6 and code.isascii() and code.isdigit()


@pytest.mark.parametrize("code", ["12345", "1234567", "12a456", "１２３４５６", " 123456", ""])
def test_only_six_ascii_digits_are_a_code(code):
    assert not portal.is_code(code)


def test_the_stored_code_is_keyed_and_bound_to_its_link():
    digest = portal.code_hash(LINK, "123456")
    assert digest == portal.code_hash(LINK, "123456")
    assert digest != portal.code_hash(uuid.uuid4(), "123456")
    assert b"123456" not in digest


def _code(**over):
    fields = {"stored_hash": portal.code_hash(LINK, "123456"), "attempts": 0}
    return fields | over


def test_the_code_matches_however_long_ago_it_was_issued():
    # No expiry (owner's decision 2026-11-24): the code stays until a new one replaces it.
    assert portal.code_matches(LINK, "123456", **_code())
    assert portal.code_matches(LINK, "123456", **_code(attempts=portal.MAX_CODE_ATTEMPTS - 1))


@pytest.mark.parametrize(
    "case",
    [
        {"code": "654321"},
        # Five wrong tries in a row block it: even the right code fails until a new one.
        {"attempts": portal.MAX_CODE_ATTEMPTS},
        {"stored_hash": None},  # no code issued
        {"code": "１２３４５６"},
    ],
)
def test_a_wrong_blocked_or_missing_code_does_not_match(case):
    code = case.pop("code", "123456")
    assert not portal.code_matches(LINK, code, **_code(**case))


def test_the_code_is_kept_for_the_specialist_to_see_and_bound_to_its_link():
    key = bytes(range(32))
    tenant = uuid.uuid4()
    blob = portal.seal_code(key, tenant, LINK, "123456")
    assert b"123456" not in blob
    assert portal.open_code(key, tenant, LINK, blob) == "123456"
    with pytest.raises(InvalidTag):
        portal.open_code(key, tenant, uuid.uuid4(), blob)


def test_code_limits_are_the_agreed_ones():
    assert not hasattr(portal, "CODE_TTL")
    assert portal.MAX_CODE_ATTEMPTS == 5
    assert timedelta(seconds=60) == portal.RESEND_AFTER
    assert portal.MAX_CODES_A_DAY == 10


def test_a_new_code_waits_a_minute_after_the_last_one():
    assert not portal.may_send_code(NOW - timedelta(seconds=59), sent_today=1, now=NOW)
    assert portal.may_send_code(NOW - timedelta(seconds=60), sent_today=1, now=NOW)
    assert portal.may_send_code(None, sent_today=0, now=NOW)
    assert not portal.may_send_code(None, sent_today=portal.MAX_CODES_A_DAY, now=NOW)


# --- The client's session ------------------------------------------------------------


def test_a_session_ends_after_an_hour_without_actions_and_after_twelve_hours_in_all():
    start = NOW - timedelta(hours=2)
    assert portal.session_alive(start, NOW - timedelta(minutes=59), NOW)
    assert not portal.session_alive(start, NOW - timedelta(hours=1), NOW)
    assert not portal.session_alive(NOW - timedelta(hours=12), NOW, NOW)


# --- What the client sees for an item -------------------------------------------------


def test_a_file_waiting_for_review_shows_as_waiting_whatever_came_before():
    assert (
        portal.client_status(PackageItemState.PRESENT, [FileStatus.ACCEPTED, FileStatus.PENDING])
        == ClientItemStatus.PENDING
    )


@pytest.mark.parametrize(
    ("state", "files", "expected"),
    [
        (None, [], ClientItemStatus.TO_UPLOAD),
        (PackageItemState.MISSING, [], ClientItemStatus.TO_UPLOAD),
        (PackageItemState.NOT_NEEDED, [FileStatus.PENDING], ClientItemStatus.NOT_NEEDED),
        (PackageItemState.PRESENT, [], ClientItemStatus.ACCEPTED),
        (None, [FileStatus.ACCEPTED], ClientItemStatus.ACCEPTED),
        # Files newest first: the latest reviewed one was sent back.
        (None, [FileStatus.RETURNED, FileStatus.ACCEPTED], ClientItemStatus.RETURNED),
        (PackageItemState.MISSING, [FileStatus.RETURNED], ClientItemStatus.RETURNED),
    ],
)
def test_the_status_of_an_item_for_the_client(state, files, expected):
    assert portal.client_status(state, files) == expected


def test_a_returned_file_needs_a_reason_and_other_needs_its_text():
    assert portal.return_reason_valid("unreadable", None)
    assert portal.return_reason_valid("need_fresh", "")
    assert portal.return_reason_valid("other", "Не та страница")
    assert not portal.return_reason_valid("other", "  ")
    assert not portal.return_reason_valid("other", None)
