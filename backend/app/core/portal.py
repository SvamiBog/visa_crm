"""Rules of the client portal (backlog 2.9; docs/decisions.md → «Портал клиента и вход по
коду»): the page where a client uploads the documents of their case's checklist.

A case has a link `/u/<token>`. The token is 256 random bits; the database looks it up by
its SHA-256 and keeps the token itself only encrypted with the workspace's key, so the
specialist can copy the link again (app/api/portal.py). A link alone opens nothing: the
client enters a code of six digits, sent by email or passed on by the specialist. The code
does not expire: it stays while the documents are collected, and only «Новый код» replaces
it (owner's decision 2026-11-24). It is checked against an HMAC with the server key bound to
its link and kept encrypted with the workspace key, so the specialist always sees it in the
case. Five wrong tries in a row block it until a new one; a wrong, blocked and missing code
look the same to the client. After the code the client has a session of their own,
unrelated to the CRM's sign-in.

A link works while the documents are collected: it closes when the case is handed over
for review (the stage «рассмотрение» or later, or an outcome) or the specialist revokes it.
"""

import hashlib
import hmac
import re
import secrets
import uuid
from collections.abc import Sequence
from datetime import datetime, timedelta
from enum import StrEnum

from app.core.config import get_settings
from app.core.crypto import decrypt, encrypt
from app.models import CaseStage, FileStatus, PackageItemState, ReturnReason, VisaOutcome

LINK_TOKEN_BYTES = 32
_LINK_TOKEN = re.compile(r"[A-Za-z0-9_-]{43}")

CODE_DIGITS = 6
# Wrong tries in a row: then the code is blocked until a new one.
MAX_CODE_ATTEMPTS = 5
RESEND_AFTER = timedelta(seconds=60)
# Codes a link gets or sends in a day, by email and by hand together: with five tries each,
# at most 50 guesses of a million a day.
MAX_CODES_A_DAY = 10
# The stages after the documents are handed over: the page is closed.
CLOSED_STAGES = frozenset({CaseStage.REVIEW, CaseStage.RESULT})

SESSION_IDLE = timedelta(hours=1)
SESSION_MAX = timedelta(hours=12)

# A file of the portal (the consent and contract files of the card keep 10 MB).
MAX_FILE_BYTES = 20 * 1024 * 1024
MAX_FILES_PER_ITEM = 10
MAX_FILES_PER_CASE = 100

MAX_RETURN_NOTE = 300


class ClientItemStatus(StrEnum):
    TO_UPLOAD = "to_upload"
    PENDING = "pending"
    ACCEPTED = "accepted"
    RETURNED = "returned"
    NOT_NEEDED = "not_needed"


# --- The link ------------------------------------------------------------------------


def new_link_token() -> str:
    return secrets.token_urlsafe(LINK_TOKEN_BYTES)


def is_link_token(token: str) -> bool:
    return _LINK_TOKEN.fullmatch(token) is not None


def link_token_hash(token: str) -> bytes:
    """A plain hash is enough: the token is 256 random bits, nothing to guess."""
    return hashlib.sha256(token.encode()).digest()


def _link_aad(tenant_id: uuid.UUID, link_id: uuid.UUID) -> bytes:
    return b"portal-link:" + tenant_id.bytes + link_id.bytes


def seal_link_token(key: bytes, tenant_id: uuid.UUID, link_id: uuid.UUID, token: str) -> bytes:
    """The token encrypted with the workspace key and bound to its row, to copy the link
    again; a copy of the database without the master key does not give it away."""
    return encrypt(key, token.encode(), _link_aad(tenant_id, link_id))


def open_link_token(key: bytes, tenant_id: uuid.UUID, link_id: uuid.UUID, blob: bytes) -> str:
    return decrypt(key, blob, _link_aad(tenant_id, link_id)).decode()


def link_open(stage: CaseStage, outcome: VisaOutcome | None, revoked_at: datetime | None) -> bool:
    return revoked_at is None and stage not in CLOSED_STAGES and outcome is None


# --- The code ------------------------------------------------------------------------


def new_code() -> str:
    return f"{secrets.randbelow(10**CODE_DIGITS):0{CODE_DIGITS}d}"


def is_code(code: str) -> bool:
    return len(code) == CODE_DIGITS and code.isascii() and code.isdigit()


def _code_key() -> bytes:
    # A key of its own: another use of SECRET_KEY never computes the same values. Outside
    # development the settings refuse a weak SECRET_KEY (app/core/config.py).
    return hmac.new(get_settings().secret_key.encode(), b"visacrm:portal-codes", "sha256").digest()


def code_hash(link_id: uuid.UUID, code: str) -> bytes:
    """A million codes are tried in no time against a plain hash; the HMAC needs the
    server key, which a copy of the database does not hold."""
    return hmac.new(_code_key(), link_id.bytes + code.encode(), "sha256").digest()


def _code_aad(tenant_id: uuid.UUID, link_id: uuid.UUID) -> bytes:
    return b"portal-code:" + tenant_id.bytes + link_id.bytes


def seal_code(key: bytes, tenant_id: uuid.UUID, link_id: uuid.UUID, code: str) -> bytes:
    """The code encrypted with the workspace key, for the specialist to see in the case."""
    return encrypt(key, code.encode(), _code_aad(tenant_id, link_id))


def open_code(key: bytes, tenant_id: uuid.UUID, link_id: uuid.UUID, blob: bytes) -> str:
    return decrypt(key, blob, _code_aad(tenant_id, link_id)).decode()


def code_matches(
    link_id: uuid.UUID, code: str, *, stored_hash: bytes | None, attempts: int
) -> bool:
    """The link's code, not blocked by wrong tries. The caller counts the try before
    asking (so tries sent at once cannot outrun the limit) and clears it on a match."""
    if stored_hash is None or not is_code(code) or attempts >= MAX_CODE_ATTEMPTS:
        return False
    return hmac.compare_digest(stored_hash, code_hash(link_id, code))


def may_send_code(last_sent_at: datetime | None, *, sent_today: int, now: datetime) -> bool:
    if sent_today >= MAX_CODES_A_DAY:
        return False
    return last_sent_at is None or now - last_sent_at >= RESEND_AFTER


# --- The client's session ------------------------------------------------------------


def session_alive(created_at: datetime, last_seen_at: datetime, now: datetime) -> bool:
    return now - last_seen_at < SESSION_IDLE and now - created_at < SESSION_MAX


# --- What the client sees ------------------------------------------------------------


def client_status(state: PackageItemState | None, files: Sequence[FileStatus]) -> ClientItemStatus:
    """The item's status on the client's page; `files` newest first. A file waiting for
    review shows first: the client sees their upload arrived."""
    if state == PackageItemState.NOT_NEEDED:
        return ClientItemStatus.NOT_NEEDED
    if FileStatus.PENDING in files:
        return ClientItemStatus.PENDING
    if state == PackageItemState.PRESENT:
        return ClientItemStatus.ACCEPTED
    if files:
        return (
            ClientItemStatus.RETURNED
            if files[0] == FileStatus.RETURNED
            else ClientItemStatus.ACCEPTED
        )
    return ClientItemStatus.TO_UPLOAD


def return_reason_valid(reason: str, note: str | None) -> bool:
    if reason not in set(ReturnReason):
        return False
    return reason != ReturnReason.OTHER or bool(note and note.strip())
