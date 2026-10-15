import uuid
from collections.abc import AsyncIterator
from functools import lru_cache

from sqlalchemy import Connection, MetaData, event, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, Session, SessionTransaction

from app.core.config import get_settings

# Database role the application switches to in every transaction. It has no BYPASSRLS,
# so Row-Level Security applies even when the connection itself logs in as a superuser
# (Railway, docker-compose). Created by the first migration.
APP_ROLE = "visacrm_app"

_SCOPE_KEY = "rls_scope"
_SYSTEM = "system"


class Base(DeclarativeBase):
    """Base class for all ORM models. Business tables must carry tenant_id (see CLAUDE.md)."""

    metadata = MetaData(
        naming_convention={
            "ix": "ix_%(column_0_label)s",
            "uq": "uq_%(table_name)s_%(column_0_name)s",
            "ck": "ck_%(table_name)s_%(constraint_name)s",
            "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
            "pk": "pk_%(table_name)s",
        }
    )


@lru_cache
def get_engine() -> AsyncEngine:
    return create_async_engine(get_settings().async_database_url, pool_pre_ping=True)


@lru_cache
def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(get_engine(), expire_on_commit=False)


async def get_session() -> AsyncIterator[AsyncSession]:
    """Unscoped session: sees no tenant rows until scope_to_tenant / scope_to_system is called."""
    async with get_sessionmaker()() as session:
        yield session


def scope_to_tenant(session: AsyncSession, tenant_id: uuid.UUID) -> None:
    """Limit the session to one tenant's rows. Call before the first query."""
    _set_scope(session, tenant_id)


def scope_to_system(session: AsyncSession) -> None:
    """Let the session see all tenants: login, admin panel. Call before the first query."""
    _set_scope(session, _SYSTEM)


def _set_scope(session: AsyncSession, scope: uuid.UUID | str) -> None:
    if session.in_transaction():
        raise RuntimeError("Set the RLS scope before the session runs its first query")
    session.info[_SCOPE_KEY] = scope


@event.listens_for(Session, "after_begin")
def _apply_rls_scope(
    session: Session, transaction: SessionTransaction, connection: Connection
) -> None:
    # SET LOCAL lasts until the transaction ends, so this runs at the start of every
    # transaction, including the ones after commit(). No scope means no tenant rows.
    scope = session.info.get(_SCOPE_KEY)
    connection.execute(text(f"SET LOCAL ROLE {APP_ROLE}"))
    connection.execute(
        text(
            "SELECT set_config('app.tenant_id', :tenant_id, true), "
            "set_config('app.system', :system, true)"
        ),
        {
            "tenant_id": str(scope) if isinstance(scope, uuid.UUID) else "",
            "system": "on" if scope == _SYSTEM else "off",
        },
    )
