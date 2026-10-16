import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import BigInteger, DateTime, Identity, Index, String, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class AuditAction(StrEnum):
    LOGIN = "auth.login"
    LOGIN_FAILED = "auth.login_failed"
    ACCOUNT_LOCKED = "auth.account_locked"
    LOGOUT = "auth.logout"
    PASSWORD_CHANGED = "auth.password_changed"
    TWO_FACTOR_ENABLED = "auth.two_factor_enabled"
    TWO_FACTOR_RESET = "auth.two_factor_reset"
    EMAIL_CHANGE_REQUESTED = "auth.email_change_requested"
    EMAIL_CHANGED = "auth.email_changed"
    ADMIN_CREATED = "admin.admin_created"
    SPECIALIST_CREATED = "admin.specialist_created"
    SPECIALIST_BLOCKED = "admin.specialist_blocked"
    SPECIALIST_UNBLOCKED = "admin.specialist_unblocked"
    SPECIALIST_PASSWORD_RESET = "admin.specialist_password_reset"
    SPECIALIST_UPDATED = "admin.specialist_updated"
    EMPLOYEE_CREATED = "employees.created"
    EMPLOYEE_UPDATED = "employees.updated"
    EMPLOYEE_BLOCKED = "employees.blocked"
    EMPLOYEE_UNBLOCKED = "employees.unblocked"
    EMPLOYEE_PASSWORD_RESET = "employees.password_reset"
    # Client data: details carry ids only, never names, phones or note text.
    CONTACT_CREATED = "contacts.created"
    CONTACT_UPDATED = "contacts.updated"
    CONTACT_NOTE_ADDED = "contacts.note_added"
    CONTACT_NOTE_EDITED = "contacts.note_edited"
    CONTACT_NOTE_DELETED = "contacts.note_deleted"
    CONTACT_LINKED = "contacts.linked"
    CONTACT_UNLINKED = "contacts.unlinked"
    QUESTIONNAIRE_UPDATED = "contacts.questionnaire_updated"
    CONSULTATION_CREATED = "consultations.created"
    CONSULTATION_UPDATED = "consultations.updated"
    CASE_CREATED = "cases.created"
    CASE_UPDATED = "cases.updated"
    CASE_GROUPED = "cases.grouped"
    CASE_UNGROUPED = "cases.ungrouped"
    CASE_QUEUED = "cases.queued"
    CASE_QUEUE_UPDATED = "cases.queue_updated"
    CASE_ATTEMPT_SENT = "cases.attempt_sent"
    CASE_ATTEMPT_UPDATED = "cases.attempt_updated"
    BOOKING_ADDED = "cases.booking_added"
    BOOKING_UPDATED = "cases.booking_updated"
    BOOKING_DELETED = "cases.booking_deleted"
    VAULT_ENTRY_CREATED = "vault.entry_created"
    VAULT_ENTRY_UPDATED = "vault.entry_updated"
    VAULT_ENTRY_DELETED = "vault.entry_deleted"
    VAULT_PASSWORD_REVEALED = "vault.password_revealed"
    # Reference data; target_type names the reference (country, vac_operator, …).
    REFERENCE_CREATED = "reference.created"
    REFERENCE_UPDATED = "reference.updated"


class AuditEvent(Base):
    """Who did what and when. Append-only: the app role cannot update or delete rows,
    and a trigger rejects UPDATE, DELETE and TRUNCATE for everyone (migration 0005)."""

    __tablename__ = "audit_events"
    __table_args__ = (Index("ix_audit_events_tenant_id_id", "tenant_id", "id"),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    # Whose workspace the event belongs to; NULL for platform-level events (admins).
    tenant_id: Mapped[uuid.UUID | None]
    actor_id: Mapped[uuid.UUID | None]
    # Copied at the time of the event: the log stays readable if the user changes later.
    actor_email: Mapped[str | None] = mapped_column(String(320))
    action: Mapped[str] = mapped_column(String(64))
    target_type: Mapped[str | None] = mapped_column(String(32))
    target_id: Mapped[str | None] = mapped_column(String(64))
    # No client personal data here: ids and short technical facts only.
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    ip: Mapped[str | None] = mapped_column(String(45))
