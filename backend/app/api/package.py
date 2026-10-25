"""The package check of a case (backlog 2.3, part 1): the case's document checklist with
what the specialist marked and each item's status at the submission date
(app/core/package.py). The items are the ones a case like this gets (app/core/checklists.py):
the shared items in use and the specialist's own, for the visa's country, the submission
country and the kind of the case's purpose.

Closed by the «clients», «cases» and «package_check» modules. The audit log gets ids and
the state only: no document names or dates.
"""

import uuid
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, TenantSession, require_module, workspace_today
from app.core.audit import record
from app.core.checklists import applies
from app.core.package import ItemStatus, item_status, reference_day, summarize
from app.models import (
    AuditAction,
    ChecklistItem,
    OwnChecklistItem,
    PackageItem,
    PackageItemState,
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
    document_date: date | None = None


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


class PackageOut(BaseModel):
    # The day freshness is counted to: the submission date, or today until there is one.
    on: date
    on_submission: bool
    ready: bool
    counts: dict[ItemStatus, int]
    items: list[PackageItemOut]


# --- Helpers -------------------------------------------------------------------------


async def _case(session: AsyncSession, case_id: uuid.UUID) -> VisaCase:
    """RLS hides other workspaces' rows, so their ids are simply not found."""
    case = await session.get(VisaCase, case_id)
    if case is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not_found")
    return case


async def _checklist(session: AsyncSession, case: VisaCase) -> list[Item]:
    """The items in use the case gets: shared first, then own, each in its list's order."""
    purpose = await session.get(VisaPurpose, case.purpose_id) if case.purpose_id else None
    kind = purpose.kind if purpose else None
    items: list[Item] = []
    for model in (ChecklistItem, OwnChecklistItem):
        query = (
            select(model)
            .where(model.country_id == case.destination_id, model.is_active)
            .order_by(model.sort_order, model.created_at)
        )
        items += await session.scalars(query)
    return [i for i in items if applies(i, case.submission_country_id, kind)]


async def _marks(session: AsyncSession, case: VisaCase) -> dict[uuid.UUID, PackageItem]:
    marks = await session.scalars(select(PackageItem).where(PackageItem.case_id == case.id))
    return {m.checklist_item_id or m.own_item_id: m for m in marks}


def _out(item: Item, mark: PackageItem | None, on: date) -> PackageItemOut:
    state = mark.state if mark else None
    document_date = mark.document_date if mark else None
    return PackageItemOut(
        item_id=item.id,
        own=isinstance(item, OwnChecklistItem),
        name=item.name,
        note=item.note,
        required=item.required,
        max_age_days=item.max_age_days,
        state=state,
        document_date=document_date,
        status=item_status(state, document_date, item.max_age_days, on),
    )


# --- Endpoints -----------------------------------------------------------------------


@router.get("/cases/{case_id}/package")
async def get_package(case_id: uuid.UUID, session: TenantSession) -> PackageOut:
    case = await _case(session, case_id)
    on = reference_day(case.submission_on, await workspace_today(session, case.tenant_id))
    marks = await _marks(session, case)
    items = [_out(i, marks.get(i.id), on) for i in await _checklist(session, case)]
    summary = summarize((i.required, i.status) for i in items)
    return PackageOut(
        on=on,
        on_submission=case.submission_on is not None,
        ready=summary.ready,
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
    case = await _case(session, case_id)
    today = await workspace_today(session, case.tenant_id)
    # A document cannot be issued later than today: a mistyped year would pass as fresh.
    if body.document_date is not None and body.document_date > today:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "document_date_in_future")
    item = next((i for i in await _checklist(session, case) if i.id == item_id), None)
    if item is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not_found")
    mark = (await _marks(session, case)).get(item.id)
    if mark is None:
        own = isinstance(item, OwnChecklistItem)
        mark = PackageItem(
            tenant_id=case.tenant_id,
            case_id=case.id,
            checklist_item_id=None if own else item.id,
            own_item_id=item.id if own else None,
        )
        session.add(mark)
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
