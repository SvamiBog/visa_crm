import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import ARRAY, DateTime, LargeBinary, Numeric, String, func, text
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
    # Tax on officially paid payments, percent (НПД in Belarus: 10).
    tax_rate: Mapped[Decimal] = mapped_column(Numeric(5, 2), server_default=text("0"))
    # Modules the admin turned on (keys from app/core/modules.py).
    modules: Mapped[list[str]] = mapped_column(ARRAY(String(40)), server_default=text("'{}'"))
    # The tenant's vault data key, encrypted with the master key (app/core/vault.py).
    vault_key_encrypted: Mapped[bytes | None] = mapped_column(LargeBinary)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
