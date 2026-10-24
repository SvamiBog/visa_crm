"""Contact (контакт): a person who came to the specialist. Lives longer than any case.

Spec: docs/clients-module.md → «Карточка контакта». Personal data: never in logs, URLs
or audit details (only ids there).
"""

import uuid
from datetime import date, datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class DocumentKind(StrEnum):
    """A signed paper kept as a file in the contact's card."""

    CONSENT = "consent"  # consent to personal data processing
    CONTRACT = "contract"


class Messenger(StrEnum):
    """A messenger besides Telegram, for clients who are not there."""

    WHATSAPP = "whatsapp"
    VIBER = "viber"


def _same_tenant(table: str, column: str, parent: str, ondelete: str) -> ForeignKeyConstraint:
    return ForeignKeyConstraint(
        [column, "tenant_id"],
        [f"{parent}.id", f"{parent}.tenant_id"],
        ondelete=ondelete,
        name=f"fk_{table}_{column}_same_tenant",
    )


class Contact(Base):
    __tablename__ = "contacts"
    __table_args__ = (
        UniqueConstraint("id", "tenant_id", name="uq_contacts_id_tenant"),
        # One card per phone number in a workspace: the duplicate guard.
        Index(
            "uq_contacts_tenant_phone",
            "tenant_id",
            "phone",
            unique=True,
            postgresql_where=text("phone IS NOT NULL"),
        ),
        _same_tenant("contacts", "lead_channel_id", "lead_channels", "RESTRICT"),
        _same_tenant("contacts", "partner_id", "partners", "RESTRICT"),
        # The whole names are joined from the parts (app/core/names.py).
        CheckConstraint("last_name IS NOT NULL OR first_name IS NOT NULL", name="has_name"),
        CheckConstraint(
            "full_name = concat_ws(' ', last_name, first_name, middle_name)",
            name="full_name_joined",
        ),
        CheckConstraint(
            "full_name_latin IS NOT DISTINCT FROM "
            "nullif(concat_ws(' ', last_name_latin, first_name_latin), '')",
            name="full_name_latin_joined",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    # ФИО: the surname, the given name, the patronymic; the surname or the given name is
    # enough. full_name is the three joined: what lists show and the search looks in.
    last_name: Mapped[str | None] = mapped_column(String(200))
    first_name: Mapped[str | None] = mapped_column(String(200))
    middle_name: Mapped[str | None] = mapped_column(String(200))
    full_name: Mapped[str] = mapped_column(Text)
    # As in the passport, for forms: the surname and the given names, and the two joined.
    last_name_latin: Mapped[str | None] = mapped_column(String(200))
    first_name_latin: Mapped[str | None] = mapped_column(String(200))
    full_name_latin: Mapped[str | None] = mapped_column(Text)
    # E.164: "+" and 8–15 digits (app/core/phone.py).
    phone: Mapped[str | None] = mapped_column(String(16))
    # Username without "@" or a display name.
    telegram: Mapped[str | None] = mapped_column(String(64))
    messenger: Mapped[Messenger | None] = mapped_column(
        Enum(Messenger, name="messenger", values_callable=lambda e: [m.value for m in e])
    )
    messenger_contact: Mapped[str | None] = mapped_column(String(64))
    email: Mapped[str | None] = mapped_column(String(320))
    birth_date: Mapped[date | None] = mapped_column(Date)
    lead_channel_id: Mapped[uuid.UUID | None]
    partner_id: Mapped[uuid.UUID | None]
    # Consent to personal data processing and the contract: signed with every client.
    consent_signed_on: Mapped[date | None] = mapped_column(Date)
    contract_number: Mapped[str | None] = mapped_column(String(50))
    contract_signed_on: Mapped[date | None] = mapped_column(Date)
    # Consultation questionnaire answers and the passport (app/core/questionnaire.py).
    questionnaire: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ContactNote(Base):
    """A dated entry in the contact's timeline (лента заметок)."""

    __tablename__ = "contact_notes"
    __table_args__ = (
        _same_tenant("contact_notes", "contact_id", "contacts", "CASCADE"),
        Index("ix_contact_notes_contact_created", "contact_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    contact_id: Mapped[uuid.UUID]
    body: Mapped[str] = mapped_column(Text)
    author_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    edited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ContactRelation(Base):
    """«related is <label> for contact»: family, who brought whom. Stored both ways, each
    side with its own label ("муж" / "жена"), so each card lists its links."""

    __tablename__ = "contact_relations"
    __table_args__ = (
        _same_tenant("contact_relations", "contact_id", "contacts", "CASCADE"),
        _same_tenant("contact_relations", "related_id", "contacts", "CASCADE"),
        UniqueConstraint("contact_id", "related_id", name="uq_contact_relations_pair"),
        CheckConstraint("contact_id <> related_id", name="not_self"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    contact_id: Mapped[uuid.UUID] = mapped_column(index=True)
    related_id: Mapped[uuid.UUID]
    label: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ContactDocument(Base):
    """A file of the consent or the contract (backlog 1.2). The row describes it; the content
    lives encrypted in the file storage under documents/<tenant>/<id> (app/core/documents.py).
    """

    __tablename__ = "contact_documents"
    __table_args__ = (
        _same_tenant("contact_documents", "contact_id", "contacts", "CASCADE"),
        Index("ix_contact_documents_contact_created", "contact_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    contact_id: Mapped[uuid.UUID]
    kind: Mapped[DocumentKind] = mapped_column(
        Enum(DocumentKind, name="document_kind", values_callable=lambda e: [m.value for m in e])
    )
    # As uploaded, cleaned (app/core/documents.py → clean_file_name).
    file_name: Mapped[str] = mapped_column(String(200))
    # Found from the file's first bytes, not taken from the browser.
    content_type: Mapped[str] = mapped_column(String(50))
    # Of the file itself, before encryption.
    size_bytes: Mapped[int] = mapped_column(Integer)
    uploaded_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
