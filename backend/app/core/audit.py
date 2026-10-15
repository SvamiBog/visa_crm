"""Writing to the audit log (app/models/audit.py).

record() adds the event to the caller's session, so it is committed or rolled back
together with the change it describes.
"""

import uuid
from contextvars import ContextVar
from typing import TYPE_CHECKING, Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit import AuditAction, AuditEvent

if TYPE_CHECKING:
    from app.api.deps import CurrentUser
    from app.models import User

# Set per request by the middleware in app/main.py (behind Railway's proxy: the real client).
client_ip: ContextVar[str | None] = ContextVar("client_ip", default=None)

_FROM_ACTOR = object()


def record(
    session: AsyncSession,
    action: AuditAction,
    *,
    actor: "User | CurrentUser | None",
    tenant_id: uuid.UUID | None | object = _FROM_ACTOR,
    target_type: str | None = None,
    target_id: uuid.UUID | str | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    """Add an audit event. tenant_id defaults to the actor's workspace."""
    if tenant_id is _FROM_ACTOR:
        tenant_id = actor.tenant_id if actor is not None else None
    assert tenant_id is None or isinstance(tenant_id, uuid.UUID)
    session.add(
        AuditEvent(
            tenant_id=tenant_id,
            actor_id=actor.id if actor is not None else None,
            actor_email=actor.email if actor is not None else None,
            action=action.value,
            target_type=target_type,
            target_id=str(target_id) if target_id is not None else None,
            details=details or {},
            ip=client_ip.get(),
        )
    )
