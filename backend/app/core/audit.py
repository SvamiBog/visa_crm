"""Writing to the audit log (app/models/audit.py).

record() adds the event to the caller's session, so it is committed or rolled back
together with the change it describes.
"""

import ipaddress
import uuid
from contextvars import ContextVar
from typing import TYPE_CHECKING, Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit import AuditAction, AuditEvent

if TYPE_CHECKING:
    from app.api.deps import CurrentUser
    from app.models import User

# Set per request by the middleware in app/main.py from client_address() below.
client_ip: ContextVar[str | None] = ContextVar("client_ip", default=None)
# What audit_events.ip holds: the longest usual way to write an address (IPv6 with an IPv4
# tail). Anything longer is not taken for one, and is refused before it is even parsed.
MAX_IP_LENGTH = 45

_FROM_ACTOR = object()


def client_address(forwarded_for: list[str], peer: str | None) -> str | None:
    """The client's address for the audit log and the login limit (backlog S17), from the
    X-Forwarded-For lines of the request and the address of its connection.

    On Railway every request comes through its proxy, which writes the address it was
    called from as the last entry of X-Forwarded-For: anything before it is what the
    client sent and proves nothing (audit N1). A developer's machine has no proxy and no
    header: the connection is the client. A value that is not an address gives None, not
    an error and not an earlier entry (audit N2)."""
    if not forwarded_for:
        return _address(peer)
    return _address(",".join(forwarded_for).rsplit(",", 1)[-1])


def _address(value: str | None) -> str | None:
    """The address written the one usual way (one client is one key of the login limit),
    or None for anything else."""
    if value is None:
        return None
    value = value.strip()
    if len(value) > MAX_IP_LENGTH:
        return None
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return None
    return str(address)


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
