# Import every model module here so Alembic autogenerate sees all tables.
from app.models.recovery_code import RecoveryCode
from app.models.session import UserSession
from app.models.tenant import Tenant
from app.models.user import TWO_FACTOR_REQUIRED_ROLES, User, UserRole

__all__ = [
    "TWO_FACTOR_REQUIRED_ROLES",
    "RecoveryCode",
    "Tenant",
    "User",
    "UserRole",
    "UserSession",
]
