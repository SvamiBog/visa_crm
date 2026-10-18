# Import every model module here so Alembic autogenerate sees all tables.
from app.models.audit import AuditAction, AuditEvent
from app.models.booking import Booking, BookingKind, BookingStatus
from app.models.checklist import ChecklistItem, OwnChecklistItem
from app.models.consultation import Consultation, Decision
from app.models.contact import (
    Contact,
    ContactDocument,
    ContactNote,
    ContactRelation,
    DocumentKind,
    Messenger,
)
from app.models.data_import import ImportKind, ImportMapping, ImportRow, ImportRun
from app.models.dictionaries import (
    Hall,
    LeadChannel,
    Partner,
    PartnerTerms,
    PartnerTermsType,
    Prepayment,
    PurposeKind,
    Registrar,
    RegistrarKind,
    RegistrarPrice,
    VisaPurpose,
)
from app.models.email_change import EmailChange
from app.models.finance import (
    CaseExpense,
    CaseFinance,
    CasePayment,
    ExchangeRate,
    RegistrarPayment,
    RegistrarPaymentMethod,
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
from app.models.reminder import ReminderDismissal
from app.models.session import UserSession
from app.models.template import ContactTemplate, DocumentTemplate
from app.models.tenant import Tenant
from app.models.trip import Trip
from app.models.user import UiTheme, User, UserRole
from app.models.vault import VaultEntry, VaultSystem, VaultSystemKind
from app.models.visa_case import (
    CaseStage,
    EmailOwner,
    SubmissionGroup,
    VisaCase,
    VisaEntries,
    VisaOutcome,
)
from app.models.visa_center_note import VisaCenterNote

__all__ = [
    "ApplicationAttempt",
    "AttemptOutcome",
    "AuditAction",
    "AuditEvent",
    "Booking",
    "BookingKind",
    "BookingStatus",
    "BookingType",
    "CaseExpense",
    "CaseFinance",
    "CasePayment",
    "CaseStage",
    "ChecklistItem",
    "Consultation",
    "Contact",
    "ContactDocument",
    "ContactNote",
    "ContactRelation",
    "ContactTemplate",
    "Country",
    "CountryRule",
    "Decision",
    "DocumentKind",
    "DocumentTemplate",
    "EmailChange",
    "EmailOwner",
    "ExchangeRate",
    "Hall",
    "ImportKind",
    "ImportMapping",
    "ImportRow",
    "ImportRun",
    "LeadChannel",
    "Messenger",
    "OwnChecklistItem",
    "Partner",
    "PartnerTerms",
    "PartnerTermsType",
    "Prepayment",
    "PurposeKind",
    "Registrar",
    "RegistrarKind",
    "RegistrarPayment",
    "RegistrarPaymentMethod",
    "RegistrarPrice",
    "ReminderDismissal",
    "SubmissionGroup",
    "RecoveryCode",
    "Tenant",
    "Trip",
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
    "VisaCenterNote",
    "VisaCenter",
    "VisaEntries",
    "VisaOutcome",
    "VisaPurpose",
    "WaitingListEntry",
]
