"""Writing to the audit log (app/models/audit.py).

record() adds the event to the caller's session, so it is committed or rolled back
together with the change it describes.
"""

import ipaddress
import uuid
from contextvars import ContextVar
from typing import TYPE_CHECKING, Any

from sqlalchemy.ext.asyncio import AsyncSession
from starlette.datastructures import Headers

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


def client_address(headers: Headers, peer: str | None) -> str | None:
    """The client's address for the audit log and the login limit (backlog S17, S22), from
    the headers of the request and the address of its connection.

    On Railway every request comes through its edge, which writes the address it was
    called from in X-Real-IP, in place of any the client sent. X-Forwarded-For is not
    read: a node of Railway's adds itself to its end (audit_events on staging got it,
    24.11.2026), and what the client sends there proves nothing (audit N1). A developer's
    machine has no proxy and no header: the connection is the client. A value that is not
    one address, or two X-Real-IP lines, give None: not an error and not another address
    in its place (audit N2)."""
    real_ip = headers.getlist("x-real-ip")
    if not real_ip:
        return _address(peer)
    if len(real_ip) > 1:
        return None
    return _address(real_ip[0])


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
