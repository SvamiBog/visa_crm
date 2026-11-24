"""The client portal of a case (backlog 2.9): the link the client opens, their sessions,
the files they upload to the items of the case's checklist, and the data notice they
acknowledged. The rules are app/core/portal.py.
"""

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    LargeBinary,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.models.user import account_fk


class CodeChannel(StrEnum):
    """How the client got the code. The Telegram bot (a later task) adds its own."""

    EMAIL = "email"
    SPECIALIST = "specialist"  # shown in the case, passed on in any messenger
    TELEGRAM = "telegram"


class FileStatus(StrEnum):
    PENDING = "pending"  # загружен клиентом, ждёт проверки
    ACCEPTED = "accepted"
    RETURNED = "returned"


class FileSource(StrEnum):
    CLIENT = "client"  # through the portal
    STAFF = "staff"  # the specialist or an employee, from the case


class ReturnReason(StrEnum):
    UNREADABLE = "unreadable"
    NEED_FRESH = "need_fresh"
    OTHER = "other"


class ConsentGivenBy(StrEnum):
    APPLICANT = "applicant"
    REPRESENTATIVE = "representative"  # a parent or guardian of a child


def _enum(enum: type[StrEnum], name: str) -> Enum:
    return Enum(enum, name=name, values_callable=lambda e: [m.value for m in e])


def _same_tenant(table: str, column: str, parent: str, ondelete: str) -> ForeignKeyConstraint:
    return ForeignKeyConstraint(
        [column, "tenant_id"],
        [f"{parent}.id", f"{parent}.tenant_id"],
        ondelete=ondelete,
        name=f"fk_{table}_{column}_same_tenant",
    )


class ClientLink(Base):
    """The portal link of a case. One live link a case; a revoked one stays for the log.
    The token is kept as its SHA-256 (the lookup) and encrypted with the workspace key (to
    copy the link again). The link's code lives on it: one at a time, kept until a new one
    replaces it; code_sent_at is when it was last issued or sent."""

    __tablename__ = "client_links"
    __table_args__ = (
        UniqueConstraint("id", "tenant_id", name="uq_client_links_id_tenant"),
        _same_tenant("client_links", "case_id", "visa_cases", "CASCADE"),
        account_fk("client_links", "created_by"),
        account_fk("client_links", "code_issued_by"),
        Index(
            "uq_client_links_live_case",
            "case_id",
            unique=True,
            postgresql_where=text("revoked_at IS NULL"),
        ),
        CheckConstraint("(code_hash IS NULL) = (code_sealed IS NULL)", name="code_kept_both_ways"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    case_id: Mapped[uuid.UUID]
    token_hash: Mapped[bytes] = mapped_column(LargeBinary, unique=True)
    token_sealed: Mapped[bytes] = mapped_column(LargeBinary)
    created_by: Mapped[uuid.UUID | None]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    code_hash: Mapped[bytes | None] = mapped_column(LargeBinary)
    code_channel: Mapped[CodeChannel | None] = mapped_column(_enum(CodeChannel, "code_channel"))
    code_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # The same code encrypted with the workspace key: the specialist sees it in the case.
    code_sealed: Mapped[bytes | None] = mapped_column(LargeBinary)
    code_attempts: Mapped[int] = mapped_column(SmallInteger, server_default=text("0"))
    code_issued_by: Mapped[uuid.UUID | None]


class ClientSession(Base):
    """A client signed in with a code. Its own cookie, unrelated to the CRM's sign-in;
    revoking the link ends it."""

    __tablename__ = "client_sessions"
    __table_args__ = (
        _same_tenant("client_sessions", "link_id", "client_links", "CASCADE"),
        _same_tenant("client_sessions", "case_id", "visa_cases", "CASCADE"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    link_id: Mapped[uuid.UUID] = mapped_column(index=True)
    case_id: Mapped[uuid.UUID]
    token_hash: Mapped[bytes] = mapped_column(LargeBinary, unique=True)
    code_channel: Mapped[CodeChannel] = mapped_column(_enum(CodeChannel, "code_channel"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class PackageFile(Base):
    """A file at an item of a case's checklist: uploaded by the client through the portal
    or by the specialist from the case. Encrypted with the workspace key in the storage
    (app/core/documents.py) under `documents/<tenant>/<id>`."""

    __tablename__ = "package_files"
    __table_args__ = (
        _same_tenant("package_files", "case_id", "visa_cases", "CASCADE"),
        _same_tenant("package_files", "own_item_id", "own_checklist_items", "RESTRICT"),
        ForeignKeyConstraint(
            ["link_id", "tenant_id"],
            ["client_links.id", "client_links.tenant_id"],
            ondelete="SET NULL (link_id)",
            name="fk_package_files_link_id_same_tenant",
        ),
        account_fk("package_files", "uploaded_by"),
        account_fk("package_files", "reviewed_by"),
        CheckConstraint("(checklist_item_id IS NULL) <> (own_item_id IS NULL)", name="one_item"),
        CheckConstraint(
            "(status = 'returned') = (return_reason IS NOT NULL)", name="reason_when_returned"
        ),
        CheckConstraint("return_reason <> 'other' OR return_note IS NOT NULL", name="other_noted"),
        Index("ix_package_files_case_created", "case_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    case_id: Mapped[uuid.UUID]
    checklist_item_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("checklist_items.id", ondelete="RESTRICT")
    )
    own_item_id: Mapped[uuid.UUID | None]
    file_name: Mapped[str] = mapped_column(String(255))
    content_type: Mapped[str] = mapped_column(String(100))
    size_bytes: Mapped[int] = mapped_column(Integer)
    source: Mapped[FileSource] = mapped_column(_enum(FileSource, "package_file_source"))
    status: Mapped[FileStatus] = mapped_column(_enum(FileStatus, "package_file_status"))
    return_reason: Mapped[ReturnReason | None] = mapped_column(
        _enum(ReturnReason, "package_file_return_reason")
    )
    return_note: Mapped[str | None] = mapped_column(String(300))
    link_id: Mapped[uuid.UUID | None]
    uploaded_by: Mapped[uuid.UUID | None]
    reviewed_by: Mapped[uuid.UUID | None]
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PortalNotice(Base):
    """A data notice exactly as a client was shown it: the platform's text of a version
    with the workspace's controller details put in. Never changed (a trigger refuses
    UPDATE); a change of the details or the text makes a new row."""

    __tablename__ = "portal_notices"
    __table_args__ = (
        UniqueConstraint("id", "tenant_id", name="uq_portal_notices_id_tenant"),
        UniqueConstraint("tenant_id", "sha256", name="uq_portal_notices_tenant_text"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"))
    version: Mapped[str] = mapped_column(String(20))
    language: Mapped[str] = mapped_column(String(5))
    body: Mapped[str] = mapped_column(Text)
    sha256: Mapped[bytes] = mapped_column(LargeBinary)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PortalConsent(Base):
    """The client's mark before the first upload: the notice acknowledged and, apart from
    it, the explicit consent to special categories of data (GDPR Art. 9(2)(a)). The proof
    the specialist keeps: never changed (a trigger refuses UPDATE)."""

    __tablename__ = "portal_consents"
    __table_args__ = (
        _same_tenant("portal_consents", "contact_id", "contacts", "CASCADE"),
        _same_tenant("portal_consents", "case_id", "visa_cases", "CASCADE"),
        _same_tenant("portal_consents", "notice_id", "portal_notices", "RESTRICT"),
        ForeignKeyConstraint(
            ["link_id", "tenant_id"],
            ["client_links.id", "client_links.tenant_id"],
            ondelete="SET NULL (link_id)",
            name="fk_portal_consents_link_id_same_tenant",
        ),
        CheckConstraint(
            "(given_by = 'representative') = (representative_name IS NOT NULL)",
            name="representative_named",
        ),
        Index("ix_portal_consents_contact_given", "contact_id", "given_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    contact_id: Mapped[uuid.UUID]
    case_id: Mapped[uuid.UUID] = mapped_column(index=True)
    link_id: Mapped[uuid.UUID | None]
    notice_id: Mapped[uuid.UUID]
    special_categories: Mapped[bool] = mapped_column(Boolean)
    given_by: Mapped[ConsentGivenBy] = mapped_column(_enum(ConsentGivenBy, "consent_given_by"))
    representative_name: Mapped[str | None] = mapped_column(String(200))
    code_channel: Mapped[CodeChannel] = mapped_column(_enum(CodeChannel, "code_channel"))
    ip: Mapped[str | None] = mapped_column(String(45))
    user_agent: Mapped[str | None] = mapped_column(String(300))
    given_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
