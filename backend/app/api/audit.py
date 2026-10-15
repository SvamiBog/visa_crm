import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import Select, select

from app.api.deps import (
    CurrentUser,
    SystemSession,
    TenantSession,
    require_admin,
    require_specialist,
)
from app.models import AuditEvent

router = APIRouter(tags=["audit"])

PAGE_SIZE = 50


class AuditEventOut(BaseModel):
    id: int
    occurred_at: datetime
    tenant_id: uuid.UUID | None
    actor_email: str | None
    action: str
    target_type: str | None
    target_id: str | None
    details: dict[str, Any]
    ip: str | None


class AuditPage(BaseModel):
    events: list[AuditEventOut]
    # Pass as ?before= to get the next (older) page; null when there is none.
    next_before: int | None


async def _page(
    session: SystemSession | TenantSession, query: Select, before: int | None, limit: int
) -> AuditPage:
    if before is not None:
        query = query.where(AuditEvent.id < before)
    rows = (await session.scalars(query.order_by(AuditEvent.id.desc()).limit(limit + 1))).all()
    events = [AuditEventOut.model_validate(row, from_attributes=True) for row in rows[:limit]]
    return AuditPage(events=events, next_before=events[-1].id if len(rows) > limit else None)


@router.get("/audit")
async def workspace_audit(
    user: Annotated[CurrentUser, Depends(require_specialist)],
    session: TenantSession,
    before: int | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = PAGE_SIZE,
) -> AuditPage:
    """Events of the specialist's workspace, including admin actions on it. Newest first."""
    query = select(AuditEvent).where(AuditEvent.tenant_id == user.tenant_id)
    return await _page(session, query, before, limit)


@router.get("/admin/audit", dependencies=[Depends(require_admin)])
async def platform_audit(
    session: SystemSession,
    before: int | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = PAGE_SIZE,
    tenant_id: uuid.UUID | None = None,
) -> AuditPage:
    """All events on the platform, optionally of one workspace. Newest first."""
    query = select(AuditEvent)
    if tenant_id is not None:
        query = query.where(AuditEvent.tenant_id == tenant_id)
    return await _page(session, query, before, limit)
