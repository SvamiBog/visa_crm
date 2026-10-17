"""Document checklists (backlog 2.2): what to collect for a visa to a country.

The shared items are kept by the admin, like the rest of the reference; a specialist adds
her own on top. Closed by the «package_check» module: a case's documents will be checked
against these lists (backlog 2.3). Everyone with the module reads them, only the
specialist changes her own items. Items are switched off, never deleted.
"""

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import AfterValidator, BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    CurrentUser,
    SystemSession,
    TenantSession,
    require_admin,
    require_module,
    require_specialist,
)
from app.core.audit import record
from app.core.checklists import for_purpose, for_submission, normalize_purposes
from app.models import (
    AuditAction,
    ChecklistItem,
    Country,
    OwnChecklistItem,
    PurposeKind,
    VisaPurpose,
)

router = APIRouter(
    prefix="/checklists",
    tags=["checklists"],
    dependencies=[Depends(require_module("package_check"))],
)
admin_router = APIRouter(
    prefix="/admin/checklists", tags=["admin"], dependencies=[Depends(require_admin)]
)

Admin = Annotated[CurrentUser, Depends(require_admin)]
Specialist = Annotated[CurrentUser, Depends(require_specialist)]

# Per country and list: a consulate's list is a page or two.
MAX_ITEMS = 200

Item = ChecklistItem | OwnChecklistItem
ItemModel = type[ChecklistItem] | type[OwnChecklistItem]


# --- Schemas -------------------------------------------------------------------------


def _strip(value: object) -> object:
    return value.strip() if isinstance(value, str) else value


def _blank_is_none(value: object) -> object:
    return (value.strip() or None) if isinstance(value, str) else value


Name = Annotated[str, Field(min_length=1, max_length=200)]
Note = Annotated[str, Field(max_length=1000)]
MaxAge = Annotated[int, Field(ge=1, le=365)]
Purposes = Annotated[list[PurposeKind], Field(max_length=20), AfterValidator(normalize_purposes)]


class ItemIn(BaseModel):
    name: Name
    submission_id: uuid.UUID | None = None
    purposes: Purposes = []
    required: bool = True
    max_age_days: MaxAge | None = None
    note: Note | None = None
    is_active: bool = True

    _name = field_validator("name", mode="before")(_strip)
    _note = field_validator("note", mode="before")(_blank_is_none)


class ItemUpdate(BaseModel):
    """Only the fields sent are changed."""

    name: Name | None = None
    submission_id: uuid.UUID | None = None
    purposes: Purposes | None = None
    required: bool | None = None
    max_age_days: MaxAge | None = None
    note: Note | None = None
    is_active: bool | None = None

    _name = field_validator("name", mode="before")(_strip)
    _note = field_validator("note", mode="before")(_blank_is_none)


NULLABLE = frozenset({"submission_id", "max_age_days", "note"})


class ItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    country_id: uuid.UUID
    submission_id: uuid.UUID | None
    purposes: list[PurposeKind]
    name: str
    note: str | None
    required: bool
    max_age_days: int | None
    sort_order: int
    is_active: bool


class Checklist(BaseModel):
    shared: list[ItemOut]
    own: list[ItemOut]


class ItemCount(BaseModel):
    country_id: uuid.UUID
    count: int


class ItemOrder(BaseModel):
    ids: Annotated[list[uuid.UUID], Field(max_length=MAX_ITEMS)]


# --- Helpers -------------------------------------------------------------------------


def _audit(
    session: AsyncSession,
    action: AuditAction,
    user: CurrentUser,
    target_type: str,
    target_id: uuid.UUID,
    name: str,
) -> None:
    """The admin's items go to the platform log, a specialist's to her workspace's."""
    record(
        session,
        action,
        actor=user,
        target_type=target_type,
        target_id=target_id,
        details={"name": name},
    )


async def _country(session: AsyncSession, country_id: uuid.UUID) -> Country:
    country = await session.get(Country, country_id)
    if country is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not_found")
    return country


async def _check_submission(session: AsyncSession, submission_id: uuid.UUID | None) -> None:
    if submission_id is not None:
        await _country(session, submission_id)


async def _items(
    session: AsyncSession, model: ItemModel, country_id: uuid.UUID, *, active_only: bool = False
) -> list[Item]:
    query = select(model).where(model.country_id == country_id)
    if active_only:
        query = query.where(model.is_active)
    return list(await session.scalars(query.order_by(model.sort_order, model.created_at)))


async def _counts(session: AsyncSession, models: tuple[ItemModel, ...]) -> list[ItemCount]:
    counts: dict[uuid.UUID, int] = {}
    for model in models:
        rows = await session.execute(
            select(model.country_id, func.count()).where(model.is_active).group_by(model.country_id)
        )
        for country_id, count in rows:
            counts[country_id] = counts.get(country_id, 0) + count
    return [ItemCount(country_id=c, count=n) for c, n in counts.items()]


async def _create(
    session: AsyncSession,
    user: CurrentUser,
    model: ItemModel,
    country_id: uuid.UUID,
    body: ItemIn,
    **scope: Any,
) -> ItemOut:
    await _country(session, country_id)
    await _check_submission(session, body.submission_id)
    items = await _items(session, model, country_id)
    if len(items) >= MAX_ITEMS:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "too_many_items")
    # A new item goes to the end of the list.
    last = max((i.sort_order for i in items), default=-1)
    item = model(country_id=country_id, sort_order=last + 1, **scope, **body.model_dump())
    session.add(item)
    await session.flush()
    _audit(session, AuditAction.REFERENCE_CREATED, user, "checklist_item", item.id, item.name)
    await session.commit()
    return ItemOut.model_validate(item)


async def _update(
    session: AsyncSession, user: CurrentUser, model: ItemModel, item_id: uuid.UUID, body: ItemUpdate
) -> ItemOut:
    # RLS hides other workspaces' items, so someone else's id is simply not found.
    item = await session.get(model, item_id)
    if item is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not_found")
    changes = body.model_dump(exclude_unset=True)
    await _check_submission(session, changes.get("submission_id"))
    for field, value in changes.items():
        if value is not None or field in NULLABLE:
            setattr(item, field, value)
    _audit(session, AuditAction.REFERENCE_UPDATED, user, "checklist_item", item.id, item.name)
    await session.commit()
    return ItemOut.model_validate(item)


async def _reorder(
    session: AsyncSession,
    user: CurrentUser,
    model: ItemModel,
    country_id: uuid.UUID,
    body: ItemOrder,
) -> list[ItemOut]:
    """The list in the order given: every item of the country's list, each once."""
    country = await _country(session, country_id)
    items = {i.id: i for i in await _items(session, model, country_id)}
    if len(body.ids) != len(items) or set(body.ids) != set(items):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "order_mismatch")
    for order, item_id in enumerate(body.ids):
        items[item_id].sort_order = order
    _audit(session, AuditAction.REFERENCE_UPDATED, user, "checklist", country.id, country.code)
    await session.commit()
    return [ItemOut.model_validate(items[i]) for i in body.ids]


# --- Workspace -----------------------------------------------------------------------


@router.get("")
async def list_countries(session: TenantSession) -> list[ItemCount]:
    """Countries with a checklist, by the number of items in use."""
    return await _counts(session, (ChecklistItem, OwnChecklistItem))


@router.get("/{country_id}")
async def get_checklist(
    country_id: uuid.UUID,
    session: TenantSession,
    submission: Annotated[uuid.UUID | None, Query()] = None,
    purpose: Annotated[uuid.UUID | None, Query()] = None,
) -> Checklist:
    """The shared items in use and all the specialist's own. With a submission country
    or one of the specialist's purposes, only the items a case like that gets."""
    await _country(session, country_id)
    kind: PurposeKind | None = None
    if purpose is not None:
        visa_purpose = await session.get(VisaPurpose, purpose)
        if visa_purpose is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "not_found")
        kind = visa_purpose.kind

    def kept(item: Item) -> bool:
        return (submission is None or for_submission(item, submission)) and (
            purpose is None or for_purpose(item, kind)
        )

    def out(items: list[Item]) -> list[ItemOut]:
        return [ItemOut.model_validate(i) for i in items if kept(i)]

    return Checklist(
        shared=out(await _items(session, ChecklistItem, country_id, active_only=True)),
        own=out(await _items(session, OwnChecklistItem, country_id)),
    )


@router.post("/{country_id}/items", status_code=status.HTTP_201_CREATED)
async def create_own_item(
    country_id: uuid.UUID, body: ItemIn, user: Specialist, session: TenantSession
) -> ItemOut:
    return await _create(
        session, user, OwnChecklistItem, country_id, body, tenant_id=user.tenant_id
    )


@router.patch("/items/{item_id}")
async def update_own_item(
    item_id: uuid.UUID, body: ItemUpdate, user: Specialist, session: TenantSession
) -> ItemOut:
    return await _update(session, user, OwnChecklistItem, item_id, body)


@router.put("/{country_id}/order")
async def order_own_items(
    country_id: uuid.UUID, body: ItemOrder, user: Specialist, session: TenantSession
) -> list[ItemOut]:
    return await _reorder(session, user, OwnChecklistItem, country_id, body)


# --- Admin ---------------------------------------------------------------------------


@admin_router.get("")
async def admin_list_countries(session: SystemSession) -> list[ItemCount]:
    return await _counts(session, (ChecklistItem,))


@admin_router.get("/{country_id}")
async def admin_get_checklist(country_id: uuid.UUID, session: SystemSession) -> list[ItemOut]:
    """Every shared item of the country, switched-off ones included."""
    await _country(session, country_id)
    return [ItemOut.model_validate(i) for i in await _items(session, ChecklistItem, country_id)]


@admin_router.post("/{country_id}/items", status_code=status.HTTP_201_CREATED)
async def create_shared_item(
    country_id: uuid.UUID, body: ItemIn, admin: Admin, session: SystemSession
) -> ItemOut:
    return await _create(session, admin, ChecklistItem, country_id, body)


@admin_router.patch("/items/{item_id}")
async def update_shared_item(
    item_id: uuid.UUID, body: ItemUpdate, admin: Admin, session: SystemSession
) -> ItemOut:
    return await _update(session, admin, ChecklistItem, item_id, body)


@admin_router.put("/{country_id}/order")
async def order_shared_items(
    country_id: uuid.UUID, body: ItemOrder, admin: Admin, session: SystemSession
) -> list[ItemOut]:
    return await _reorder(session, admin, ChecklistItem, country_id, body)
