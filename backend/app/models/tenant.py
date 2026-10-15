import uuid
from datetime import datetime

from sqlalchemy import DateTime, String, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class Tenant(Base):
    """A specialist's workspace (визовик = арендатор). All business data belongs to one tenant."""

    __tablename__ = "tenants"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(200))
    base_currency: Mapped[str] = mapped_column(String(3), server_default="BYN")
    timezone: Mapped[str] = mapped_column(String(64), server_default="Europe/Minsk")
    is_active: Mapped[bool] = mapped_column(server_default=text("true"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
