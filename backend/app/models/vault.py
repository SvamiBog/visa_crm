"""The vault of accounts (сейф учётных записей; docs/clients-module.md → «Сейф учётных
записей»): logins on visa center and embassy sites, mailboxes, Booking.

Passwords are encrypted with the workspace's key (app/core/vault.py) and bound to their
entry, so a blob copied onto another entry does not open. An entry without a contact is
the workspace's own account: a backup mailbox of the specialist or their Booking account.
"""

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    LargeBinary,
    SmallInteger,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.models.visa_case import EmailOwner


def _same_tenant(column: str, parent: str, ondelete: str) -> ForeignKeyConstraint:
    return ForeignKeyConstraint(
        [column, "tenant_id"],
        [f"{parent}.id", f"{parent}.tenant_id"],
        ondelete=ondelete,
        name=f"fk_vault_entries_{column}_same_tenant",
    )


class VaultSystemKind(StrEnum):
    EMAIL = "email"
    VISA_CENTER = "visa_center"  # VFS, TLScontact, BLS…
    EMBASSY = "embassy"
    BOOKING = "booking"
    OTHER = "other"


class VaultSystem(Base):
    """A site accounts are made on, with its password rules for the generator
    (справочник «Системы и требования к паролям»). Switched off rather than deleted."""

    __tablename__ = "vault_systems"
    __table_args__ = (
        UniqueConstraint("tenant_id", "name", name="uq_vault_systems_tenant_name"),
        UniqueConstraint("id", "tenant_id", name="uq_vault_systems_id_tenant"),
        CheckConstraint("password_length BETWEEN 6 AND 64", name="password_length_range"),
        CheckConstraint(
            "password_lower OR password_upper OR password_digits OR password_symbols <> ''",
            name="password_has_characters",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(100))
    kind: Mapped[VaultSystemKind] = mapped_column(
        Enum(
            VaultSystemKind,
            name="vault_system_kind",
            values_callable=lambda e: [m.value for m in e],
        )
    )
    url: Mapped[str | None] = mapped_column(String(300))
    # Generator rules: the length and which characters a password must contain.
    password_length: Mapped[int] = mapped_column(SmallInteger, server_default=text("12"))
    password_lower: Mapped[bool] = mapped_column(server_default=text("true"))
    password_upper: Mapped[bool] = mapped_column(server_default=text("true"))
    password_digits: Mapped[bool] = mapped_column(server_default=text("true"))
    # The special characters the site accepts; empty — none.
    password_symbols: Mapped[str] = mapped_column(String(32), server_default="!@#$%*?")
    sort_order: Mapped[int] = mapped_column(SmallInteger, server_default=text("0"))
    is_active: Mapped[bool] = mapped_column(server_default=text("true"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class VaultEntry(Base):
    """One account: whose it is, the login and the encrypted password."""

    __tablename__ = "vault_entries"
    __table_args__ = (
        _same_tenant("system_id", "vault_systems", "RESTRICT"),
        _same_tenant("contact_id", "contacts", "CASCADE"),
        _same_tenant("case_id", "visa_cases", "SET NULL (case_id)"),
        Index("ix_vault_entries_contact_id", "contact_id"),
        CheckConstraint("case_id IS NULL OR contact_id IS NOT NULL", name="case_needs_contact"),
        # Bookings refer to the account they were made in, within the same workspace.
        UniqueConstraint("id", "tenant_id", name="uq_vault_entries_id_tenant"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    system_id: Mapped[uuid.UUID]
    # The applicant whose account it is; NULL — the workspace's own account.
    contact_id: Mapped[uuid.UUID | None]
    case_id: Mapped[uuid.UUID | None]
    login: Mapped[str] = mapped_column(String(320))
    # Whose mailbox the account is on: not to forget where the letters go.
    email_owner: Mapped[EmailOwner | None] = mapped_column(
        Enum(
            EmailOwner,
            name="email_owner",
            values_callable=lambda e: [m.value for m in e],
            create_type=False,
        )
    )
    # AES-GCM under the workspace key, bound to this entry (app/core/vault.py).
    password_encrypted: Mapped[bytes | None] = mapped_column(LargeBinary)
    password_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # The specialist made the account by hand (a mark kept from their spreadsheet).
    created_manually: Mapped[bool] = mapped_column(server_default=text("false"))
    # E.g. the username at the Greek visa center (2 letters + 2 digits).
    extra_id: Mapped[str | None] = mapped_column(String(100))
    note: Mapped[str | None] = mapped_column(String(1000))
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
