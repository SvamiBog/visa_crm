"""The passport's data is removed a week after the decision (backlog 2.8; docs/decisions.md →
«Удаление паспортных данных после решения», owner's decision 2026-11-24).

What goes: the fields of PURGED_FIELDS in the contact's questionnaire, and the files at the
checklist items of the client's cases (the client portal, backlog 2.9: uploaded by the
client or added by the specialist; the package marks stay). A client with such files and
no passport data is due all the same. Everything else stays for the next trip: the name, the date of
birth, the nationality and sex, contacts, the vault, the consent and the contract, trips,
visas, cases, payments, notes and the package.

When: AFTER_DECISION_DAYS after the latest decision of the client (any result), only once
every case of theirs is at the result stage. A result without a date of decision counts
from the day it was entered; and no passport goes sooner than AFTER_ENTRY_DAYS after its
result was entered, so «Требует внимания» always shows it first (NOTICE_DAYS before).
«Отложить» keeps it POSTPONE_DAYS more; the first run after the release keeps everything
already due until the release + 14 days (migration 0046).

Where: always on the stands (local, test, staging, PR stands); in production off until
PASSPORT_PURGE_FROM (owner's decision 2026-11-24: questions about the feature first). Off,
nothing is removed, listed or answered; the switch day counts as a first run of its own
(`with_grace`).

The daily run (`keep_purging`, started in app/main.py like the rates) removes what is due
in each workspace in that workspace's own session.
"""

import asyncio
import logging
import uuid
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import ARRAY, bindparam, delete, exists, func, or_, select, text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import storage
from app.core.audit import record
from app.core.clock import today_in
from app.core.config import get_settings
from app.core.db import get_sessionmaker, scope_to_system, scope_to_tenant
from app.core.documents import object_key
from app.models import AuditAction, CaseStage, Contact, PackageFile, Tenant, VisaCase

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
# Grace after the switch in production: what is already due waits this long (as the
# release's first run did).
FIRST_RUN_DAYS = 14
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


def switched_on(env: str, switched_on_from: date | None, today: date) -> bool:
    """Always on the stands; in production only from PASSPORT_PURGE_FROM on."""
    if env != "production":
        return True
    return switched_on_from is not None and switched_on_from <= today


def with_grace(
    purge_day: date, notice_from: date | None, switched_on_from: date | None
) -> tuple[date, date | None]:
    """The switch in production is a first run of its own: what would go sooner than
    FIRST_RUN_DAYS after it goes on that day and is listed from the switch, so nothing is
    removed the day it is switched on (the release's first run kept a client until a day
    that may be past by then)."""
    if switched_on_from is None:
        return purge_day, notice_from
    grace = switched_on_from + timedelta(days=FIRST_RUN_DAYS)
    if purge_day >= grace:
        return purge_day, notice_from
    return grace, min(notice_from, switched_on_from) if notice_from else switched_on_from


def is_on(today: date) -> bool:
    """Whether the removal works on this site today (the daily run, «Требует внимания» and
    its buttons)."""
    settings = get_settings()
    return switched_on(settings.env, settings.passport_purge_from, today)


def postponed_until(purge_day: date, today: date) -> date:
    """«Отложить на 30 дней»: from the day it was due, or from today if that has passed."""
    return max(purge_day, today) + timedelta(days=POSTPONE_DAYS)


def filled_fields(questionnaire: dict) -> list[str]:
    """The fields of the passport that hold something, in PURGED_FIELDS order."""
    return [field for field in PURGED_FIELDS if questionnaire.get(field) is not None]


def brings_back(
    current: dict, sent: dict, opened_at: datetime | None, updated_at: datetime
) -> bool:
    """Whether a form opened from another version of the card would fill passport fields
    that are empty now (backlog A11; docs/decisions.md → «Удаление паспортных данных после
    решения»). The removal changes the card's `updated_at`: what the form shows was removed
    meanwhile, by the daily run or «Удалить сейчас», and must not come back. The version is
    a mark, not a time: the daily run stamps the start of its transaction, so a card saved
    during the run gets an older `updated_at` again. A request without a version (the
    passport read from a photo, the API) enters new data on purpose and is let through."""
    if opened_at is None or opened_at == updated_at:
        return False
    return any(current.get(f) is None and sent.get(f) is not None for f in PURGED_FIELDS)


@dataclass(frozen=True)
class Due:
    contact_id: uuid.UUID
    contact_name: str
    purge_on: date
    notice_on: date
    fields: list[str]
    # Files at the checklist items of the client's cases (the client portal).
    files: int = 0


@dataclass(frozen=True)
class Purged:
    fields: list[str]
    # Storage keys of the files whose rows went: removed from the storage after the commit
    # (remove_objects).
    objects: list[str]


_CASES = text("SELECT * FROM passport_purge_cases(:ids)").bindparams(
    bindparam("ids", type_=ARRAY(PG_UUID(as_uuid=True)))
)


async def dues(
    session: AsyncSession, timezone: str, contact_id: uuid.UUID | None = None
) -> list[Due]:
    """The clients of the session's workspace whose passport has a removal day. The cases
    are read through passport_purge_cases: an employee's session does not see the cases
    not given to them, and those still count (migration 0046)."""
    files = (
        select(func.count(PackageFile.id))
        .join(VisaCase, VisaCase.id == PackageFile.case_id)
        .where(VisaCase.contact_id == Contact.id)
        .scalar_subquery()
    )
    query = select(
        Contact.id,
        Contact.full_name,
        Contact.questionnaire,
        Contact.passport_kept_until,
        Contact.passport_notice_from,
        files.label("files"),
    ).where(
        or_(
            *(Contact.questionnaire[field].astext.is_not(None) for field in PURGED_FIELDS),
            files > 0,
        ),
        exists().where(VisaCase.contact_id == Contact.id, VisaCase.outcome.is_not(None)),
    )
    if contact_id is not None:
        query = query.where(Contact.id == contact_id)
    contacts = (await session.execute(query)).all()
    if not contacts:
        return []
    zone = ZoneInfo(timezone)
    switched_on_from = get_settings().passport_purge_from
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
            day, notice_from = with_grace(day, c.passport_notice_from, switched_on_from)
            found.append(
                Due(
                    contact_id=c.id,
                    contact_name=c.full_name,
                    purge_on=day,
                    notice_on=notice_on(day, notice_from),
                    fields=filled_fields(c.questionnaire),
                    files=c.files,
                )
            )
    return found


async def purge_client_uploads(session: AsyncSession, contact_id: uuid.UUID) -> list[str]:
    """The files at the checklist items of the client's cases (the client portal), in the
    same transaction as the passport; returns their storage keys. An employee's session
    takes only the cases given to them: the rest go with the next daily run."""
    rows = (
        await session.execute(
            delete(PackageFile)
            .where(
                PackageFile.case_id.in_(
                    select(VisaCase.id).where(VisaCase.contact_id == contact_id)
                )
            )
            .returning(PackageFile.tenant_id, PackageFile.id)
        )
    ).all()
    return [object_key(tenant_id, file_id) for tenant_id, file_id in rows]


async def purge_contact(session: AsyncSession, contact: Contact) -> Purged:
    """Remove the passport's fields and the client's files. The caller writes the audit
    event, commits, then removes the objects (remove_objects)."""
    fields = filled_fields(contact.questionnaire)
    contact.questionnaire = {**contact.questionnaire, **dict.fromkeys(PURGED_FIELDS)}
    contact.passport_kept_until = None
    contact.passport_notice_from = None
    return Purged(fields, await purge_client_uploads(session, contact.id))


async def remove_objects(keys: Iterable[str]) -> None:
    """Remove the stored files whose rows are gone, after the commit."""
    for key in keys:
        try:
            await storage.delete_object(key)
        except (storage.StorageNotConfigured, storage.StorageFailed):
            # No row refers to it: what is left is ciphertext nobody can open through the
            # app. The key holds ids only.
            logger.warning("Object %s of a removed file was not removed", key)


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
    objects: list[str] = []
    async with get_sessionmaker()() as session:
        scope_to_tenant(session, tenant_id)
        today = today_in(timezone)
        if not is_on(today):
            return 0
        for due in await dues(session, timezone):
            if due.purge_on > today:
                continue
            contact = await locked_due(session, timezone, due.contact_id)
            if contact is None or contact[1].purge_on > today:
                continue  # put off or removed by hand meanwhile
            purged = await purge_contact(session, contact[0])
            record(
                session,
                AuditAction.PASSPORT_PURGED,
                actor=None,
                tenant_id=tenant_id,
                target_type="contact",
                target_id=contact[0].id,
                details={"fields": purged.fields, "files": len(purged.objects)},
            )
            objects += purged.objects
            removed += 1
        await session.commit()
    await remove_objects(objects)
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
    if not found or not (found[0].fields or found[0].files):
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
