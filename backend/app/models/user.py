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
    SmallInteger,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class UserRole(StrEnum):
    ADMIN = "admin"  # platform owner, admin panel, no tenant
    SPECIALIST = "specialist"  # визовик, owns a tenant
    EMPLOYEE = "employee"  # сотрудник визовика, only the modules and cases granted


class UiTheme(StrEnum):
    """Interface styles from docs/prototype/Styles.dc.html; each user picks one in settings."""

    BLUE = "blue"  # A, the default
    DARK = "dark"  # B
    WARM = "warm"  # C
    MINIMAL = "minimal"  # D


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint("(role = 'admin') = (tenant_id IS NULL)", name="admin_has_no_tenant"),
        CheckConstraint("email = lower(email)", name="email_lowercase"),
        # Target of (user_id, tenant_id) keys: a grant cannot name another workspace's user.
        UniqueConstraint("id", "tenant_id", name="uq_users_id_tenant"),
        CheckConstraint("daily_card_limit BETWEEN 1 AND 1000", name="daily_card_limit_range"),
        CheckConstraint(
            "daily_password_limit BETWEEN 1 AND 1000", name="daily_password_limit_range"
        ),
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
    # Employees only: cards and passwords a day (app/core/employee_limits.py, Т.14).
    daily_card_limit: Mapped[int] = mapped_column(SmallInteger, server_default=text("50"))
    daily_password_limit: Mapped[int] = mapped_column(SmallInteger, server_default=text("20"))
    ui_theme: Mapped[str] = mapped_column(String(16), server_default=UiTheme.BLUE.value)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
