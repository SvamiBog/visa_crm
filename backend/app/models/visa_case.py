"""Case (кейс): one visa for one applicant. A family applying together is a submission
group (группа подачи) of several cases, each with its own country, price and result.

Spec: docs/clients-module.md → «Кейс в работе», «Этапы кейса». The table is `visa_cases`
because `case` is reserved in SQL. Queues, vault, bookings and finances hang on the case
in later tasks (backlog 1.6–1.9).
"""

import uuid
from datetime import date, datetime
from enum import StrEnum

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    SmallInteger,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.models.dictionaries import Hall


def _enum(cls: type[StrEnum], name: str, *, create: bool = True) -> Enum:
    return Enum(cls, name=name, values_callable=lambda e: [m.value for m in e], create_type=create)


def _same_tenant(column: str, parent: str, ondelete: str) -> ForeignKeyConstraint:
    return ForeignKeyConstraint(
        [column, "tenant_id"],
        [f"{parent}.id", f"{parent}.tenant_id"],
        ondelete=ondelete,
        name=f"fk_visa_cases_{column}_same_tenant",
    )


class CaseStage(StrEnum):
    """Kanban columns after the consultation, in order (app/core/cases.py moves them on)."""

    WAITING = "waiting"  # ожидание записи: queue, 60-day window or a slot
    DOCUMENTS = "documents"  # сбор документов: after the appointment date has come
    SUBMISSION = "submission"  # подача
    REVIEW = "review"  # рассмотрение
    RESULT = "result"  # результат


class VisaOutcome(StrEnum):
    VISA = "visa"
    REFUSAL = "refusal"
    # Got a date but declined right before submitting: kept in the funnel, not lost.
    NOT_SUBMITTED = "not_submitted"


class VisaEntries(StrEnum):
    SINGLE = "single"
    DOUBLE = "double"
    MULTIPLE = "multiple"


class EmailOwner(StrEnum):
    """Whose mailbox the visa center account and the application are on."""

    CLIENT = "client"
    SPECIALIST = "specialist"  # created by the specialist for the client
    SPECIALIST_BACKUP = "specialist_backup"  # one of the specialist's own backup mailboxes
    REGISTRAR = "registrar"


class SubmissionGroup(Base):
    """Cases of people who apply together (a family): seen in each member's card."""

    __tablename__ = "submission_groups"
    __table_args__ = (UniqueConstraint("id", "tenant_id", name="uq_submission_groups_id_tenant"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class VisaCase(Base):
    __tablename__ = "visa_cases"
    __table_args__ = (
        UniqueConstraint("tenant_id", "number", name="uq_visa_cases_tenant_number"),
        UniqueConstraint("id", "tenant_id", name="uq_visa_cases_id_tenant"),
        _same_tenant("contact_id", "contacts", "CASCADE"),
        # Leaving the last group member or removing a consultation keeps the case.
        _same_tenant("group_id", "submission_groups", "SET NULL (group_id)"),
        _same_tenant("consultation_id", "consultations", "SET NULL (consultation_id)"),
        _same_tenant("purpose_id", "visa_purposes", "RESTRICT"),
        _same_tenant("registrar_id", "registrars", "RESTRICT"),
        Index("ix_visa_cases_contact_id", "contact_id"),
        Index("ix_visa_cases_group_id", "group_id"),
        Index("ix_visa_cases_tenant_stage", "tenant_id", "stage"),
        CheckConstraint("number > 0", name="number_positive"),
        CheckConstraint(
            "outcome = 'visa' OR (visa_entries IS NULL AND visa_valid_from IS NULL "
            "AND visa_valid_to IS NULL AND stay_days IS NULL)",
            name="visa_details_only_for_visa",
        ),
        CheckConstraint(
            "visa_valid_from IS NULL OR visa_valid_to IS NULL OR visa_valid_to >= visa_valid_from",
            name="visa_validity_order",
        ),
        CheckConstraint(
            "stay_days IS NULL OR stay_days BETWEEN 1 AND 3660", name="stay_days_range"
        ),
        CheckConstraint(
            "decided_on IS NULL OR outcome IS NOT NULL", name="decided_on_with_outcome"
        ),
        CheckConstraint(
            "NOT after_appeal OR outcome IN ('visa', 'refusal')", name="appeal_needs_decision"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    # «Кейс №127»: numbered per workspace (app/api/cases.py takes the next number).
    number: Mapped[int] = mapped_column(Integer)
    # The applicant: every family member has their own contact card.
    contact_id: Mapped[uuid.UUID]
    group_id: Mapped[uuid.UUID | None]
    # Where the case came from: its fee counts towards the price (backlog 1.9).
    consultation_id: Mapped[uuid.UUID | None]

    destination_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("countries.id", ondelete="RESTRICT"), index=True
    )
    purpose_id: Mapped[uuid.UUID | None]
    # Where it is submitted: Belarus (any city) or Turkey, Serbia, Georgia, Armenia; the
    # visa center belongs to the rule «destination × submission country».
    submission_country_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("countries.id", ondelete="RESTRICT")
    )
    visa_center_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("visa_centers.id", ondelete="RESTRICT")
    )
    registrar_id: Mapped[uuid.UUID | None]
    hall: Mapped[Hall | None] = mapped_column(_enum(Hall, "visa_center_hall", create=False))
    # The client's number at the visa center; what it is (ID, application number, embassy
    # form number, username) comes from the country rule. Said with the email on calls.
    vac_number: Mapped[str | None] = mapped_column(String(64))
    application_email: Mapped[str | None] = mapped_column(String(320))
    application_email_owner: Mapped[EmailOwner | None] = mapped_column(
        _enum(EmailOwner, "email_owner")
    )

    stage: Mapped[CaseStage] = mapped_column(
        _enum(CaseStage, "case_stage"), server_default=CaseStage.WAITING.value
    )
    # Appointment date in the visa center or embassy (дата подачи).
    submission_on: Mapped[date | None] = mapped_column(Date)
    # The specialist's questionnaire for the application form: when the client is to send
    # it back, and when it came (a reminder in between, backlog 1.19).
    questionnaire_due_on: Mapped[date | None] = mapped_column(Date)
    questionnaire_received_on: Mapped[date | None] = mapped_column(Date)

    # Result: five structured fields instead of one list of hundreds of variants.
    outcome: Mapped[VisaOutcome | None] = mapped_column(_enum(VisaOutcome, "visa_outcome"))
    decided_on: Mapped[date | None] = mapped_column(Date)
    visa_entries: Mapped[VisaEntries | None] = mapped_column(_enum(VisaEntries, "visa_entries"))
    visa_valid_from: Mapped[date | None] = mapped_column(Date)
    visa_valid_to: Mapped[date | None] = mapped_column(Date)
    stay_days: Mapped[int | None] = mapped_column(SmallInteger)
    after_appeal: Mapped[bool] = mapped_column(server_default=text("false"))

    created_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class EmployeeCase(Base):
    """A case the specialist gave an employee (вопрос 17, docs/decisions.md). An employee
    sees only these cases, the other cases of their submission groups, the contacts of
    those cases, and the contacts and cases they created; Row-Level Security enforces it
    (policy employee_scope, migration 0031)."""

    __tablename__ = "employee_cases"
    __table_args__ = (
        ForeignKeyConstraint(
            ["user_id", "tenant_id"],
            ["users.id", "users.tenant_id"],
            ondelete="CASCADE",
            name="fk_employee_cases_user_id_same_tenant",
        ),
        ForeignKeyConstraint(
            ["case_id", "tenant_id"],
            ["visa_cases.id", "visa_cases.tenant_id"],
            ondelete="CASCADE",
            name="fk_employee_cases_case_id_same_tenant",
        ),
        Index("ix_employee_cases_case_id", "case_id"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    case_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    granted_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
