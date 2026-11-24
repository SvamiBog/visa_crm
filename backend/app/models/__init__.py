# Import every model module here so Alembic autogenerate sees all tables.
from app.models.audit import AuditAction, AuditEvent
from app.models.booking import Booking, BookingKind, BookingStatus
from app.models.checklist import ChecklistItem, OwnChecklistItem
from app.models.consultation import Consultation, Decision
from app.models.contact import (
    Contact,
    ContactDocument,
    ContactNote,
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
from app.models.package import PackageItem, PackageItemState
from app.models.portal import (
    ClientLink,
    ClientSession,
    CodeChannel,
    ConsentGivenBy,
    FileSource,
    FileStatus,
    PackageFile,
    PortalConsent,
    PortalNotice,
    ReturnReason,
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
from app.models.subscription import SubscriptionPayment, Tariff
from app.models.template import ContactTemplate, DocumentTemplate
from app.models.tenant import Tenant
from app.models.trip import Trip
from app.models.user import UiTheme, User, UserRole
from app.models.vault import VaultEntry, VaultSystem, VaultSystemKind
from app.models.visa_case import (
    CaseStage,
    EmailOwner,
    EmployeeCase,
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
    "ClientLink",
    "ClientSession",
    "CodeChannel",
    "ConsentGivenBy",
    "Consultation",
    "Contact",
    "ContactDocument",
    "ContactNote",
    "ContactTemplate",
    "Country",
    "CountryRule",
    "Decision",
    "DocumentKind",
    "DocumentTemplate",
    "EmailChange",
    "EmailOwner",
    "EmployeeCase",
    "ExchangeRate",
    "FileSource",
    "FileStatus",
    "Hall",
    "ImportKind",
    "ImportMapping",
    "ImportRow",
    "ImportRun",
    "LeadChannel",
    "Messenger",
    "OwnChecklistItem",
    "PackageFile",
    "PackageItem",
    "PackageItemState",
    "Partner",
    "PartnerTerms",
    "PartnerTermsType",
    "PortalConsent",
    "PortalNotice",
    "Prepayment",
    "PurposeKind",
    "RecoveryCode",
    "Registrar",
    "RegistrarKind",
    "RegistrarPayment",
    "RegistrarPaymentMethod",
    "RegistrarPrice",
    "ReminderDismissal",
    "ReturnReason",
    "SubmissionGroup",
    "SubscriptionPayment",
    "Tariff",
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
    "VisaCenter",
    "VisaCenterNote",
    "VisaEntries",
    "VisaOutcome",
    "VisaPurpose",
    "WaitingListEntry",
]
