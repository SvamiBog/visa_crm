"""The passport's data is removed a week after the decision (backlog 2.8; docs/decisions.md →
«Удаление паспортных данных после решения», owner's decision 2026-11-24).

What goes: the fields of PURGED_FIELDS in the contact's questionnaire, and the documents
the client uploaded (none are kept yet: `purge_client_uploads` is where the client portal
will remove its files). Everything else stays for the next trip: the name, the date of
birth, the nationality and sex, contacts, the vault, the consent and the contract, trips,
visas, cases, payments, notes and the package.

When: AFTER_DECISION_DAYS after the latest decision of the client (any result), only once
every case of theirs is at the result stage. A result without a date of decision counts
from the day it was entered; and no passport goes sooner than AFTER_ENTRY_DAYS after its
result was entered, so «Требует внимания» always shows it first (NOTICE_DAYS before).
«Отложить» keeps it POSTPONE_DAYS more; the first run after the release keeps everything
already due until the release + 14 days (migration 0046).

The daily run (`keep_purging`, started in app/main.py like the rates) removes what is due
in each workspace in that workspace's own session.
"""

import asyncio
import logging
import uuid
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import ARRAY, bindparam, exists, or_, select, text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import record
from app.core.clock import today_in
from app.core.db import get_sessionmaker, scope_to_system, scope_to_tenant
from app.models import AuditAction, CaseStage, Contact, Tenant, VisaCase

logger = logging.getLogger(__name__)

PURGED_FIELDS = (
    "passport_number",
    "personal_number",
    "passport_issued_on",
    "passport_expires_on",
    "passport_issued_by",
    "birth_place",
)
AFTER_DECISION_DAYS = 7
AFTER_ENTRY_DAYS = 2
NOTICE_DAYS = 2
POSTPONE_DAYS = 30
# Rounds of the daily run: a few a day, so a restart delays a removal by hours at most.
RUN_EVERY = 6 * 3600  # seconds


@dataclass(frozen=True)
class Decision:
    """One case's result: the date of decision, if known, and the day it was entered."""

    decided_on: date | None
    entered_on: date | None


def purge_on(
    decisions: Iterable[Decision], *, open_case: bool, kept_until: date | None
) -> date | None:
    """The day the passport goes; None while a case is in work or nothing is decided."""
    days = [_purge_day(d) for d in decisions]
    if open_case or not days:
        return None
    day = max(days)
    return max(day, kept_until) if kept_until else day


def _purge_day(decision: Decision) -> date:
    counted_from = decision.decided_on or decision.entered_on
    assert counted_from is not None
    day = counted_from + timedelta(days=AFTER_DECISION_DAYS)
    if decision.entered_on is not None:
        day = max(day, decision.entered_on + timedelta(days=AFTER_ENTRY_DAYS))
    return day


def notice_on(purge_day: date, notice_from: date | None) -> date:
    """The day «Требует внимания» starts showing the removal."""
    day = purge_day - timedelta(days=NOTICE_DAYS)
    return min(day, notice_from) if notice_from else day


def postponed_until(purge_day: date, today: date) -> date:
    """«Отложить на 30 дней»: from the day it was due, or from today if that has passed."""
    return max(purge_day, today) + timedelta(days=POSTPONE_DAYS)


def filled_fields(questionnaire: dict) -> list[str]:
    """The fields of the passport that hold something, in PURGED_FIELDS order."""
    return [field for field in PURGED_FIELDS if questionnaire.get(field) is not None]


@dataclass(frozen=True)
class Due:
    contact_id: uuid.UUID
    contact_name: str
    purge_on: date
    notice_on: date
    fields: list[str]


_CASES = text("SELECT * FROM passport_purge_cases(:ids)").bindparams(
    bindparam("ids", type_=ARRAY(PG_UUID(as_uuid=True)))
)


async def dues(
    session: AsyncSession, timezone: str, contact_id: uuid.UUID | None = None
) -> list[Due]:
    """The clients of the session's workspace whose passport has a removal day. The cases
    are read through passport_purge_cases: an employee's session does not see the cases
    not given to them, and those still count (migration 0046)."""
    query = select(
        Contact.id,
        Contact.full_name,
        Contact.questionnaire,
        Contact.passport_kept_until,
        Contact.passport_notice_from,
    ).where(
        or_(*(Contact.questionnaire[field].astext.is_not(None) for field in PURGED_FIELDS)),
        exists().where(VisaCase.contact_id == Contact.id, VisaCase.outcome.is_not(None)),
    )
    if contact_id is not None:
        query = query.where(Contact.id == contact_id)
    contacts = (await session.execute(query)).all()
    if not contacts:
        return []
    zone = ZoneInfo(timezone)
    decisions: dict[uuid.UUID, list[Decision]] = defaultdict(list)
    in_work: set[uuid.UUID] = set()
    for case in await session.execute(_CASES, {"ids": [c.id for c in contacts]}):
        if case.stage != CaseStage.RESULT:
            in_work.add(case.contact_id)
        entered = case.decision_entered_at
        entered_on = entered.astimezone(zone).date() if entered else None
        if case.outcome is not None and (case.decided_on or entered_on):
            decisions[case.contact_id].append(Decision(case.decided_on, entered_on))
    found = []
    for c in contacts:
        day = purge_on(decisions[c.id], open_case=c.id in in_work, kept_until=c.passport_kept_until)
        if day is not None:
            found.append(
                Due(
                    contact_id=c.id,
                    contact_name=c.full_name,
                    purge_on=day,
                    notice_on=notice_on(day, c.passport_notice_from),
                    fields=filled_fields(c.questionnaire),
                )
            )
    return found


async def purge_client_uploads(session: AsyncSession, contact_id: uuid.UUID) -> None:
    """The documents the client uploaded. None are kept yet: the client portal will remove
    its files (package_files) here, in the same transaction as the passport."""


async def purge_contact(session: AsyncSession, contact: Contact) -> list[str]:
    """Remove the passport's fields and the client's uploads; returns the fields removed.
    The caller writes the audit event and commits."""
    fields = filled_fields(contact.questionnaire)
    contact.questionnaire = {**contact.questionnaire, **dict.fromkeys(PURGED_FIELDS)}
    contact.passport_kept_until = None
    contact.passport_notice_from = None
    await purge_client_uploads(session, contact.id)
    return fields


async def purge_due() -> int:
    """The daily run: remove every passport due today or earlier, workspace by workspace,
    each in its own session; returns how many were removed. A workspace that fails is
    logged by its id and the others go on."""
    async with get_sessionmaker()() as session:
        scope_to_system(session)
        workspaces = (await session.execute(select(Tenant.id, Tenant.timezone))).all()
    removed = 0
    for tenant_id, timezone in workspaces:
        try:
            removed += await _purge_workspace(tenant_id, timezone)
        except Exception as error:  # the next round tries this workspace again
            logger.warning(
                "Removing passport data in workspace %s failed: %s",
                tenant_id,
                type(error).__name__,
            )
    return removed


async def _purge_workspace(tenant_id: uuid.UUID, timezone: str) -> int:
    removed = 0
    async with get_sessionmaker()() as session:
        scope_to_tenant(session, tenant_id)
        today = today_in(timezone)
        for due in await dues(session, timezone):
            if due.purge_on > today:
                continue
            contact = await locked_due(session, timezone, due.contact_id)
            if contact is None or contact[1].purge_on > today:
                continue  # put off or removed by hand meanwhile
            fields = await purge_contact(session, contact[0])
            record(
                session,
                AuditAction.PASSPORT_PURGED,
                actor=None,
                tenant_id=tenant_id,
                target_type="contact",
                target_id=contact[0].id,
                details={"fields": fields},
            )
            removed += 1
        await session.commit()
    return removed


async def locked_due(
    session: AsyncSession, timezone: str, contact_id: uuid.UUID
) -> tuple[Contact, Due] | None:
    """The client, locked until the end of the transaction, and their removal day counted
    again under the lock: «Отложить», «Удалить сейчас» and the daily run do not cross.
    None if the client is not seen or nothing is to be removed."""
    contact = await session.get(Contact, contact_id, with_for_update=True)
    if contact is None:
        return None
    found = await dues(session, timezone, contact.id)
    if not found or not found[0].fields:
        return None
    return contact, found[0]


async def keep_purging() -> None:
    """Run the removal while the web process runs: at the start and every few hours, so a
    restart or a failed round costs hours, not a day (it removes only what is due)."""
    while True:
        try:
            removed = await purge_due()
            if removed:
                logger.info("Passport data removed after the decision: %d clients", removed)
        except Exception as error:  # the next round tries again
            logger.warning("Removing passport data failed: %s", type(error).__name__)
        await asyncio.sleep(RUN_EVERY)
