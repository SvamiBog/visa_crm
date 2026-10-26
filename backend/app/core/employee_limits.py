"""Daily limits of an employee (backlog Т.14, docs/decisions.md → «Защита данных от
копирования сотрудником»): how many client cards and cases an employee may open in a day
and how many vault passwords they may reveal. The same card opened again that day does
not count twice. Over the limit the employee is paused until the next day in the
specialist's time zone, and the specialist sees it in «Требует внимания».
"""

import uuid
from collections.abc import Collection
from datetime import UTC, datetime, time
from enum import StrEnum
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import record
from app.core.clock import today_in
from app.models import AuditAction, AuditEvent, Tenant, User, UserRole

if TYPE_CHECKING:
    from app.api.deps import CurrentUser


class LimitKind(StrEnum):
    CARDS = "cards"  # client cards and cases opened
    PASSWORDS = "passwords"  # vault passwords revealed


MAX_LIMIT = 1000  # as the users table checks; the defaults (50 and 20) are its own

# What counts against each limit: one event a card a day, one a password revealed.
_COUNTED = {
    LimitKind.CARDS: (AuditAction.CONTACT_VIEWED, AuditAction.CASE_VIEWED),
    LimitKind.PASSWORDS: (AuditAction.VAULT_PASSWORD_REVEALED,),
}


class LimitReached(Exception):
    """The employee has opened as much as the specialist allows today."""

    def __init__(self, kind: LimitKind) -> None:
        super().__init__(kind.value)
        self.kind = kind
        self.code = "card_limit_reached" if kind == LimitKind.CARDS else "password_limit_reached"


def over_limit(seen_today: Collection[str], target: str, limit: int) -> bool:
    """Would opening `target` go over the limit? What was opened today is free again."""
    return target not in seen_today and len(set(seen_today)) >= limit


def day_start(timezone: str) -> datetime:
    """The start of today in the specialist's time zone, as an aware UTC moment."""
    midnight = datetime.combine(today_in(timezone), time.min, ZoneInfo(timezone))
    return midnight.astimezone(UTC)


def limit_of(employee: User, kind: LimitKind) -> int:
    return employee.daily_card_limit if kind == LimitKind.CARDS else employee.daily_password_limit


async def watch(
    session: AsyncSession,
    user: "CurrentUser",
    kind: LimitKind,
    target_type: str,
    target_id: uuid.UUID,
    opened: AuditAction | None = None,
) -> None:
    """Before an employee opens a card, a case or a password: raise LimitReached past
    today's limit, telling the specialist once a day; otherwise log the open (`opened`),
    once a card a day. A specialist is neither limited nor logged here. Commits."""
    if user.role != UserRole.EMPLOYEE:
        return
    employee = await session.get_one(User, user.id)
    tenant = await session.get_one(Tenant, user.tenant_id)
    since = day_start(tenant.timezone)
    today = select(AuditEvent).where(
        AuditEvent.actor_id == user.id, AuditEvent.occurred_at >= since
    )
    seen = set(
        await session.scalars(
            today.with_only_columns(AuditEvent.target_id)
            .where(AuditEvent.action.in_(_COUNTED[kind]))
            .distinct()
        )
    )
    target = str(target_id)
    limit = limit_of(employee, kind)
    if over_limit(seen, target, limit):
        told = await session.scalar(
            today.with_only_columns(AuditEvent.id).where(
                AuditEvent.action == AuditAction.EMPLOYEE_LIMIT_REACHED,
                AuditEvent.details["kind"].astext == kind.value,
            )
        )
        if told is None:
            record(
                session,
                AuditAction.EMPLOYEE_LIMIT_REACHED,
                actor=user,
                target_type="user",
                target_id=user.id,
                details={"kind": kind.value, "limit": limit},
            )
            await session.commit()
        raise LimitReached(kind)
    if opened is not None and target not in seen:
        record(session, opened, actor=user, target_type=target_type, target_id=target_id)
        await session.commit()
