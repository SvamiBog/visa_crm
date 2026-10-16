# Import every model module here so Alembic autogenerate sees all tables.
from app.models.audit import AuditAction, AuditEvent
from app.models.consultation import Consultation, Decision
from app.models.contact import Contact, ContactNote, ContactRelation, Messenger
from app.models.dictionaries import (
    Hall,
    LeadChannel,
    Partner,
    PartnerTerms,
    PartnerTermsType,
    Prepayment,
    Registrar,
    RegistrarKind,
    RegistrarPrice,
    VisaPurpose,
)
from app.models.queue import ApplicationAttempt, AttemptOutcome, WaitingListEntry
from app.models.recovery_code import RecoveryCode
from app.models.reference import (
    BookingType,
    Country,
    CountryRule,
    VacIdentifierType,
    VacOperator,
    VisaCenter,
)
from app.models.session import UserSession
from app.models.tenant import Tenant
from app.models.user import TWO_FACTOR_REQUIRED_ROLES, UiTheme, User, UserRole
from app.models.vault import VaultEntry, VaultSystem, VaultSystemKind
from app.models.visa_case import (
    CaseStage,
    EmailOwner,
    SubmissionGroup,
    VisaCase,
    VisaEntries,
    VisaOutcome,
)

__all__ = [
    "ApplicationAttempt",
    "AttemptOutcome",
    "AuditAction",
    "AuditEvent",
    "BookingType",
    "CaseStage",
    "Consultation",
    "Contact",
    "ContactNote",
    "ContactRelation",
    "Country",
    "CountryRule",
    "Decision",
    "EmailOwner",
    "Hall",
    "LeadChannel",
    "Messenger",
    "Partner",
    "PartnerTerms",
    "PartnerTermsType",
    "Prepayment",
    "Registrar",
    "RegistrarKind",
    "RegistrarPrice",
    "SubmissionGroup",
    "TWO_FACTOR_REQUIRED_ROLES",
    "RecoveryCode",
    "Tenant",
    "UiTheme",
    "User",
    "UserRole",
    "UserSession",
    "VacIdentifierType",
    "VacOperator",
    "VaultEntry",
    "VaultSystem",
    "VaultSystemKind",
    "VisaCase",
    "VisaCenter",
    "VisaEntries",
    "VisaOutcome",
    "VisaPurpose",
    "WaitingListEntry",
]
