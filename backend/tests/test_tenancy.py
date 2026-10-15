import uuid

import pytest
from sqlalchemy import func, select, text, update
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.core.db import scope_to_system, scope_to_tenant
from app.models import Tenant, User, UserRole

TENANT_A = uuid.UUID("00000000-0000-0000-0000-00000000000a")
TENANT_B = uuid.UUID("00000000-0000-0000-0000-00000000000b")


@pytest.fixture
async def two_tenants(owner_engine: AsyncEngine) -> None:
    async with owner_engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO tenants (id, name) VALUES (:a, 'Specialist A'), (:b, 'Specialist B')"
            ),
            {"a": TENANT_A, "b": TENANT_B},
        )
        await conn.execute(
            text(
                "INSERT INTO users (id, tenant_id, email, role) VALUES "
                "(gen_random_uuid(), NULL, 'admin@example.com', 'admin'), "
                "(gen_random_uuid(), :a, 'a@example.com', 'specialist'), "
                "(gen_random_uuid(), :a, 'a-staff@example.com', 'employee'), "
                "(gen_random_uuid(), :b, 'b@example.com', 'specialist')"
            ),
            {"a": TENANT_A, "b": TENANT_B},
        )


async def emails(session: AsyncSession) -> set[str]:
    return set((await session.scalars(select(User.email))).all())


@pytest.mark.usefixtures("two_tenants")
async def test_tenant_sees_only_own_rows(sessionmaker: async_sessionmaker[AsyncSession]) -> None:
    async with sessionmaker() as session:
        scope_to_tenant(session, TENANT_B)
        assert await emails(session) == {"b@example.com"}
        assert (await session.scalars(select(Tenant.name))).all() == ["Specialist B"]


@pytest.mark.usefixtures("two_tenants")
async def test_raw_sql_is_isolated_too(sessionmaker: async_sessionmaker[AsyncSession]) -> None:
    async with sessionmaker() as session:
        scope_to_tenant(session, TENANT_B)
        rows = (await session.execute(text("SELECT email FROM users"))).scalars().all()
        assert rows == ["b@example.com"]
        leaked = await session.scalar(
            text("SELECT count(*) FROM users WHERE tenant_id = :a"), {"a": TENANT_A}
        )
        assert leaked == 0


@pytest.mark.usefixtures("two_tenants")
async def test_tenant_cannot_change_other_tenant_rows(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    async with sessionmaker() as session:
        scope_to_tenant(session, TENANT_B)
        result = await session.execute(
            update(User).where(User.tenant_id == TENANT_A).values(is_active=False)
        )
        assert result.rowcount == 0
        deleted = await session.execute(
            text("DELETE FROM users WHERE tenant_id = :a"), {"a": TENANT_A}
        )
        assert deleted.rowcount == 0


@pytest.mark.usefixtures("two_tenants")
async def test_tenant_cannot_insert_into_other_tenant(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    async with sessionmaker() as session:
        scope_to_tenant(session, TENANT_B)
        session.add(User(tenant_id=TENANT_A, email="planted@example.com", role=UserRole.EMPLOYEE))
        with pytest.raises(DBAPIError, match="row-level security"):
            await session.flush()


@pytest.mark.usefixtures("two_tenants")
async def test_unscoped_session_sees_nothing(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    async with sessionmaker() as session:
        assert await emails(session) == set()
        assert await session.scalar(select(func.count()).select_from(Tenant)) == 0


@pytest.mark.usefixtures("two_tenants")
async def test_system_scope_sees_all_tenants(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    async with sessionmaker() as session:
        scope_to_system(session)
        assert len(await emails(session)) == 4


@pytest.mark.usefixtures("two_tenants")
async def test_scope_survives_commit(sessionmaker: async_sessionmaker[AsyncSession]) -> None:
    async with sessionmaker() as session:
        scope_to_tenant(session, TENANT_A)
        session.add(User(tenant_id=TENANT_A, email="a-new@example.com", role=UserRole.EMPLOYEE))
        await session.commit()
        # A new transaction starts here; it must be scoped to tenant A again.
        assert "b@example.com" not in await emails(session)
        assert "a-new@example.com" in await emails(session)


async def test_scope_cannot_change_mid_transaction(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    async with sessionmaker() as session:
        scope_to_tenant(session, TENANT_A)
        await session.execute(select(1))
        with pytest.raises(RuntimeError):
            scope_to_tenant(session, TENANT_B)


@pytest.mark.parametrize(
    ("tenant_id", "role"),
    [(TENANT_A, UserRole.ADMIN), (None, UserRole.SPECIALIST), (None, UserRole.EMPLOYEE)],
)
@pytest.mark.usefixtures("two_tenants")
async def test_admin_has_no_tenant_and_others_do(
    sessionmaker: async_sessionmaker[AsyncSession], tenant_id: uuid.UUID | None, role: UserRole
) -> None:
    async with sessionmaker() as session:
        scope_to_system(session)
        session.add(User(tenant_id=tenant_id, email="x@example.com", role=role))
        with pytest.raises(IntegrityError, match="admin_has_no_tenant"):
            await session.flush()


@pytest.mark.usefixtures("two_tenants")
async def test_email_must_be_lowercase(sessionmaker: async_sessionmaker[AsyncSession]) -> None:
    async with sessionmaker() as session:
        scope_to_tenant(session, TENANT_A)
        session.add(User(tenant_id=TENANT_A, email="Mixed@Example.com", role=UserRole.EMPLOYEE))
        with pytest.raises(IntegrityError, match="email_lowercase"):
            await session.flush()
