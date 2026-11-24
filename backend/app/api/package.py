"""The package check of a case (backlog 2.3): the case's document checklist with what the
specialist marked and each item's status at the submission date (app/core/package.py).
The items are the ones a case like this gets (app/core/checklists.py): the shared items in
use and the specialist's own, for the visa's country, the submission country and the kind
of the case's purpose. `package_summaries` gives the readiness of many cases at once to
«Требует внимания» and the home screen.

Closed by the «clients», «cases» and «package_check» modules. The audit log gets ids and
the state only: no document names or dates.
"""

import uuid
from collections import defaultdict
from collections.abc import Sequence
from datetime import date, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, TenantSession, require_module, workspace_today
from app.core.audit import record
from app.core.checklists import applies
from app.core.days import Day
from app.core.employee_limits import LimitKind, watch
from app.core.package import ItemStatus, Summary, item_status, reference_day, summarize
from app.models import (
    AuditAction,
    ChecklistItem,
    FileSource,
    FileStatus,
    OwnChecklistItem,
    PackageFile,
    PackageItem,
    PackageItemState,
    PurposeKind,
    ReturnReason,
    User,
    VisaCase,
    VisaPurpose,
)

router = APIRouter(
    tags=["package"],
    dependencies=[
        Depends(require_module("clients")),
        Depends(require_module("cases")),
        Depends(require_module("package_check")),
    ],
)

Member = Annotated[CurrentUser, Depends(require_module("package_check"))]

Item = ChecklistItem | OwnChecklistItem


# --- Schemas -------------------------------------------------------------------------


class Mark(BaseModel):
    state: PackageItemState
    document_date: Day | None = None


class PackageFileOut(BaseModel):
    """A file at the item (backlog 2.9): from the client's portal or added from the case."""

    id: uuid.UUID
    file_name: str
    content_type: str
    size_bytes: int
    source: FileSource
    status: FileStatus
    return_reason: ReturnReason | None
    return_note: str | None
    uploaded_by_email: str | None
    created_at: datetime
    reviewed_at: datetime | None


class PackageItemOut(BaseModel):
    item_id: uuid.UUID
    own: bool
    name: str
    note: str | None
    required: bool
    max_age_days: int | None
    state: PackageItemState | None
    document_date: date | None
    status: ItemStatus
    # Newest first.
    files: list[PackageFileOut] = []


class PackageOut(BaseModel):
    # The day freshness is counted to: the submission date, or today until there is one.
    on: date
    on_submission: bool
    ready: bool
    # Required documents still to collect: missing, undated or expired.
    missing: int
    counts: dict[ItemStatus, int]
    items: list[PackageItemOut]


# --- Helpers -------------------------------------------------------------------------


async def case_of(session: AsyncSession, user: CurrentUser, case_id: uuid.UUID) -> VisaCase:
    """RLS hides other workspaces' rows, so their ids are simply not found. The package is
    a section of the client's card: reading or marking it opens the card for an employee's
    daily limit (Т.14), before anything is read or changed."""
    case = await session.get(VisaCase, case_id)
    if case is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not_found")
    await watch(
        session, user, LimitKind.CARDS, "contact", case.contact_id, AuditAction.CONTACT_VIEWED
    )
    return case


async def _checklists(
    session: AsyncSession, cases: Sequence[VisaCase]
) -> dict[uuid.UUID, list[Item]]:
    """The items in use each case gets: shared first, then own, each in its list's order."""
    purpose_ids = {c.purpose_id for c in cases if c.purpose_id}
    kinds: dict[uuid.UUID, PurposeKind | None] = {}
    if purpose_ids:
        rows = await session.execute(
            select(VisaPurpose.id, VisaPurpose.kind).where(VisaPurpose.id.in_(purpose_ids))
        )
        kinds = dict(rows.all())
    items: list[Item] = []
    for model in (ChecklistItem, OwnChecklistItem):
        query = (
            select(model)
            .where(model.country_id.in_({c.destination_id for c in cases}), model.is_active)
            .order_by(model.sort_order, model.created_at)
        )
        items += await session.scalars(query)
    return {
        c.id: [
            i
            for i in items
            if i.country_id == c.destination_id
            and applies(i, c.submission_country_id, kinds.get(c.purpose_id))
        ]
        for c in cases
    }


async def _marks(
    session: AsyncSession, case_ids: Sequence[uuid.UUID]
) -> dict[uuid.UUID, dict[uuid.UUID, PackageItem]]:
    """Each case's marks by the item they are for."""
    marks: dict[uuid.UUID, dict[uuid.UUID, PackageItem]] = defaultdict(dict)
    for m in await session.scalars(select(PackageItem).where(PackageItem.case_id.in_(case_ids))):
        marks[m.case_id][m.checklist_item_id or m.own_item_id] = m
    return marks


def _status(item: Item, mark: PackageItem | None, on: date) -> ItemStatus:
    if mark is None:
        return item_status(None, None, item.max_age_days, on)
    return item_status(mark.state, mark.document_date, item.max_age_days, on)


def file_out(file: PackageFile, uploaded_by_email: str | None) -> PackageFileOut:
    return PackageFileOut(
        id=file.id,
        file_name=file.file_name,
        content_type=file.content_type,
        size_bytes=file.size_bytes,
        source=file.source,
        status=file.status,
        return_reason=file.return_reason,
        return_note=file.return_note,
        uploaded_by_email=uploaded_by_email,
        created_at=file.created_at,
        reviewed_at=file.reviewed_at,
    )


async def files_of(session: AsyncSession, case_id: uuid.UUID) -> dict[uuid.UUID, list[PackageFile]]:
    """The case's files by the item they are at, newest first."""
    files: dict[uuid.UUID, list[PackageFile]] = defaultdict(list)
    query = (
        select(PackageFile)
        .where(PackageFile.case_id == case_id)
        .order_by(PackageFile.created_at.desc(), PackageFile.id.desc())
    )
    for f in await session.scalars(query):
        files[f.checklist_item_id or f.own_item_id].append(f)
    return files


async def checklist_item(session: AsyncSession, case: VisaCase, item_id: uuid.UUID) -> Item:
    """An item the case gets; any other id is not found."""
    checklist = (await _checklists(session, [case]))[case.id]
    item = next((i for i in checklist if i.id == item_id), None)
    if item is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not_found")
    return item


async def checklist_of(session: AsyncSession, case: VisaCase) -> list[Item]:
    return (await _checklists(session, [case]))[case.id]


async def mark_of(session: AsyncSession, case: VisaCase, item: Item) -> PackageItem:
    """The case's mark for the item, a new one (not yet added state) when there is none."""
    mark = (await _marks(session, [case.id]))[case.id].get(item.id)
    if mark is None:
        own = isinstance(item, OwnChecklistItem)
        mark = PackageItem(
            tenant_id=case.tenant_id,
            case_id=case.id,
            checklist_item_id=None if own else item.id,
            own_item_id=item.id if own else None,
        )
        session.add(mark)
    return mark


async def marks_of(session: AsyncSession, case: VisaCase) -> dict[uuid.UUID, PackageItem]:
    return (await _marks(session, [case.id]))[case.id]


async def check_document_date(
    session: AsyncSession, case: VisaCase, document_date: date | None
) -> None:
    """A document cannot be issued later than today: a mistyped year would pass as fresh."""
    today = await workspace_today(session, case.tenant_id)
    if document_date is not None and document_date > today:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "document_date_in_future")


def _out(item: Item, mark: PackageItem | None, on: date) -> PackageItemOut:
    return PackageItemOut(
        item_id=item.id,
        own=isinstance(item, OwnChecklistItem),
        name=item.name,
        note=item.note,
        required=item.required,
        max_age_days=item.max_age_days,
        state=mark.state if mark else None,
        document_date=mark.document_date if mark else None,
        status=_status(item, mark, on),
    )


async def package_summaries(
    session: AsyncSession, cases: Sequence[VisaCase], today: date
) -> dict[uuid.UUID, Summary]:
    """Readiness of each case's package at its submission date (or today). A case whose
    checklist is empty is left out: there is nothing to collect, so nothing to show."""
    if not cases:
        return {}
    checklists = await _checklists(session, cases)
    marks = await _marks(session, [c.id for c in cases])
    summaries: dict[uuid.UUID, Summary] = {}
    for case in cases:
        if not checklists[case.id]:
            continue
        on = reference_day(case.submission_on, today)
        summaries[case.id] = summarize(
            (i.required, _status(i, marks[case.id].get(i.id), on)) for i in checklists[case.id]
        )
    return summaries


async def _emails(session: AsyncSession, ids: set[uuid.UUID | None]) -> dict[uuid.UUID, str]:
    ids.discard(None)
    if not ids:
        return {}
    return dict((await session.execute(select(User.id, User.email).where(User.id.in_(ids)))).all())


# --- Endpoints -----------------------------------------------------------------------


@router.get("/cases/{case_id}/package")
async def get_package(case_id: uuid.UUID, user: Member, session: TenantSession) -> PackageOut:
    case = await case_of(session, user, case_id)
    on = reference_day(case.submission_on, await workspace_today(session, case.tenant_id))
    marks = (await _marks(session, [case.id]))[case.id]
    checklist = (await _checklists(session, [case]))[case.id]
    files = await files_of(session, case.id)
    emails = await _emails(session, {f.uploaded_by for fs in files.values() for f in fs})
    items = [
        _out(i, marks.get(i.id), on).model_copy(
            update={"files": [file_out(f, emails.get(f.uploaded_by)) for f in files.get(i.id, [])]}
        )
        for i in checklist
    ]
    summary = summarize((i.required, i.status) for i in items)
    return PackageOut(
        on=on,
        on_submission=case.submission_on is not None,
        ready=summary.ready,
        missing=summary.missing,
        counts=summary.counts,
        items=items,
    )


@router.put("/cases/{case_id}/package/{item_id}")
async def mark_item(
    case_id: uuid.UUID, item_id: uuid.UUID, body: Mark, user: Member, session: TenantSession
) -> PackageItemOut:
    """Mark a document of the case's checklist; only items the case gets can be marked."""
    if body.document_date is not None and body.state != PackageItemState.PRESENT:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "document_date_needs_present")
    case = await case_of(session, user, case_id)
    today = await workspace_today(session, case.tenant_id)
    await check_document_date(session, case, body.document_date)
    item = await checklist_item(session, case, item_id)
    mark = await mark_of(session, case, item)
    mark.state = body.state
    mark.document_date = body.document_date
    await session.flush()
    record(
        session,
        AuditAction.PACKAGE_ITEM_MARKED,
        actor=user,
        target_type="case",
        target_id=case.id,
        details={"contact": str(case.contact_id), "item": str(item.id), "state": body.state.value},
    )
    await session.commit()
    return _out(item, mark, reference_day(case.submission_on, today))
