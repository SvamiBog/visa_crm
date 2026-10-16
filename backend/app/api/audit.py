"""Reading the audit log. The admin sees the whole platform and filters it; a specialist
sees only what was done by the people of their workspace (themselves and their
employees) — not the admin's actions on it.

Filters go in the query string: they are ids, dates and action names, never personal
data of clients.
"""

import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import Select, func, select

from app.api.deps import (
    CurrentUser,
    SystemSession,
    TenantSession,
    current_user,
    require_admin,
    require_specialist,
)
from app.models import AuditAction, AuditEvent, User, UserRole

router = APIRouter(tags=["audit"])

PAGE_SIZE = 50


class AuditEventOut(BaseModel):
    id: int
    occurred_at: datetime
    tenant_id: uuid.UUID | None
    actor_id: uuid.UUID | None
    actor_email: str | None
    action: str
    target_type: str | None
    target_id: str | None
    details: dict[str, Any]
    ip: str | None


class AuditPage(BaseModel):
    events: list[AuditEventOut]
    # Pass as ?cursor= (with the same order) for the next page; null when there is none.
    next_cursor: int | None


class Actor(BaseModel):
    id: uuid.UUID
    email: str


class Filters:
    """Query parameters shared by both logs."""

    def __init__(
        self,
        order: Annotated[Literal["desc", "asc"], Query()] = "desc",
        cursor: Annotated[int | None, Query()] = None,
        limit: Annotated[int, Query(ge=1, le=200)] = PAGE_SIZE,
        actor_id: Annotated[uuid.UUID | None, Query()] = None,
        category: Annotated[str | None, Query(pattern=r"^[a-z_]+$")] = None,
        action: Annotated[str | None, Query(pattern=r"^[a-z_]+\.[a-z_]+$")] = None,
        occurred_from: Annotated[datetime | None, Query()] = None,
        occurred_to: Annotated[datetime | None, Query()] = None,
    ) -> None:
        self.order = order
        self.cursor = cursor
        self.limit = limit
        self.actor_id = actor_id
        self.category = category
        self.action = action
        self.occurred_from = occurred_from
        self.occurred_to = occurred_to


async def _page(session: SystemSession | TenantSession, query: Select, f: Filters) -> AuditPage:
    if f.actor_id is not None:
        query = query.where(AuditEvent.actor_id == f.actor_id)
    if f.category is not None:
        query = query.where(AuditEvent.action.startswith(f"{f.category}.", autoescape=True))
    if f.action is not None:
        query = query.where(AuditEvent.action == f.action)
    if f.occurred_from is not None:
        query = query.where(AuditEvent.occurred_at >= f.occurred_from)
    if f.occurred_to is not None:
        query = query.where(AuditEvent.occurred_at < f.occurred_to)
    # Ids grow with time, so they order the log and page it without gaps.
    if f.order == "desc":
        if f.cursor is not None:
            query = query.where(AuditEvent.id < f.cursor)
        query = query.order_by(AuditEvent.id.desc())
    else:
        if f.cursor is not None:
            query = query.where(AuditEvent.id > f.cursor)
        query = query.order_by(AuditEvent.id)
    rows = (await session.scalars(query.limit(f.limit + 1))).all()
    events = [AuditEventOut.model_validate(row, from_attributes=True) for row in rows[: f.limit]]
    return AuditPage(events=events, next_cursor=events[-1].id if len(rows) > f.limit else None)


def _workspace_people(tenant_id: uuid.UUID | None) -> Select:
    return select(User.id).where(User.tenant_id == tenant_id)


@router.get("/audit/actions")
async def audit_actions(user: Annotated[CurrentUser, Depends(current_user)]) -> list[str]:
    """Every kind of event, for the filters (labels are in the locales)."""
    if user.role not in (UserRole.ADMIN, UserRole.SPECIALIST):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "forbidden")
    return [a.value for a in AuditAction]


@router.get("/audit")
async def workspace_audit(
    user: Annotated[CurrentUser, Depends(require_specialist)],
    session: TenantSession,
    filters: Annotated[Filters, Depends()],
) -> AuditPage:
    """What the specialist and their employees did. The admin's actions on the workspace
    and platform commands are not shown here."""
    query = select(AuditEvent).where(
        AuditEvent.tenant_id == user.tenant_id,
        AuditEvent.actor_id.in_(_workspace_people(user.tenant_id)),
    )
    return await _page(session, query, filters)


@router.get("/audit/actors")
async def workspace_actors(
    user: Annotated[CurrentUser, Depends(require_specialist)], session: TenantSession
) -> list[Actor]:
    """The specialist and their employees, for the «who» filter."""
    rows = await session.execute(
        select(User.id, User.email).where(User.tenant_id == user.tenant_id).order_by(User.email)
    )
    return [Actor(id=i, email=e) for i, e in rows.tuples()]


@router.get("/admin/audit", dependencies=[Depends(require_admin)])
async def platform_audit(
    session: SystemSession,
    filters: Annotated[Filters, Depends()],
    tenant_id: Annotated[uuid.UUID | None, Query()] = None,
    platform: Annotated[bool, Query()] = False,
) -> AuditPage:
    """All events on the platform; of one workspace, or (`platform`) only those outside
    any workspace: admins' logins, reference changes, commands."""
    query = select(AuditEvent)
    if platform:
        query = query.where(AuditEvent.tenant_id.is_(None))
    elif tenant_id is not None:
        query = query.where(AuditEvent.tenant_id == tenant_id)
    return await _page(session, query, filters)


@router.get("/admin/audit/actors", dependencies=[Depends(require_admin)])
async def platform_actors(session: SystemSession) -> list[Actor]:
    """Everyone who has an event in the log, with the email of their latest event."""
    latest = (
        select(AuditEvent.actor_id, func.max(AuditEvent.id).label("last"))
        .where(AuditEvent.actor_id.is_not(None))
        .group_by(AuditEvent.actor_id)
        .subquery()
    )
    rows = await session.execute(
        select(AuditEvent.actor_id, AuditEvent.actor_email)
        .join(latest, latest.c.last == AuditEvent.id)
        .order_by(AuditEvent.actor_email)
    )
    return [Actor(id=i, email=e) for i, e in rows.tuples() if i is not None and e is not None]
