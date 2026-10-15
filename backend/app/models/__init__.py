# Import every model module here so Alembic autogenerate sees all tables.
from app.models.session import UserSession
from app.models.tenant import Tenant
from app.models.user import User, UserRole

__all__ = ["Tenant", "User", "UserRole", "UserSession"]
