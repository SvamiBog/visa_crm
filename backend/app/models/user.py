import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
    ARRAY,
    BigInteger,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    LargeBinary,
    String,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class UserRole(StrEnum):
    ADMIN = "admin"  # platform owner, admin panel, no tenant
    SPECIALIST = "specialist"  # визовик, owns a tenant
    EMPLOYEE = "employee"  # сотрудник визовика, only the modules and cases granted


# Roles that must turn on two-factor login before using the app.
TWO_FACTOR_REQUIRED_ROLES = frozenset({UserRole.ADMIN, UserRole.SPECIALIST})


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint("(role = 'admin') = (tenant_id IS NULL)", name="admin_has_no_tenant"),
        CheckConstraint("email = lower(email)", name="email_lowercase"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("tenants.id", ondelete="RESTRICT"), index=True
    )
    email: Mapped[str] = mapped_column(String(320), unique=True)
    full_name: Mapped[str | None] = mapped_column(String(200))
    # NULL until a password is set: such a user cannot log in.
    password_hash: Mapped[str | None] = mapped_column(String(255))
    role: Mapped[UserRole] = mapped_column(
        Enum(UserRole, name="user_role", values_callable=lambda e: [m.value for m in e])
    )
    must_change_password: Mapped[bool] = mapped_column(server_default=text("true"))
    is_active: Mapped[bool] = mapped_column(server_default=text("true"))
    failed_login_count: Mapped[int] = mapped_column(server_default=text("0"))
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # TOTP secret sealed with the master key; set during setup, active once enabled_at is set.
    totp_secret_encrypted: Mapped[bytes | None] = mapped_column(LargeBinary)
    totp_enabled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Last accepted TOTP time step: the same code is never accepted twice.
    totp_last_step: Mapped[int | None] = mapped_column(BigInteger)
    # Employees only: the specialist's modules this employee may use.
    modules: Mapped[list[str]] = mapped_column(ARRAY(String(40)), server_default=text("'{}'"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
