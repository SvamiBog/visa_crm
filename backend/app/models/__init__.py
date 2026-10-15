# Import every model module here so Alembic autogenerate sees all tables.
from app.models.audit import AuditAction, AuditEvent
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

__all__ = [
    "AuditAction",
    "AuditEvent",
    "BookingType",
    "Country",
    "CountryRule",
    "Hall",
    "LeadChannel",
    "Partner",
    "PartnerTerms",
    "PartnerTermsType",
    "Prepayment",
    "Registrar",
    "RegistrarKind",
    "RegistrarPrice",
    "TWO_FACTOR_REQUIRED_ROLES",
    "RecoveryCode",
    "Tenant",
    "UiTheme",
    "User",
    "UserRole",
    "UserSession",
    "VacIdentifierType",
    "VacOperator",
    "VisaCenter",
    "VisaPurpose",
]
