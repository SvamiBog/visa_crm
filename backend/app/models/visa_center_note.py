"""The specialist's remarks about a visa center («замечания визовика по центру», backlog
1.13): kept apart from the center, which the builder edits (backlog 1.20)."""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, ForeignKeyConstraint, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class VisaCenterNote(Base):
    __tablename__ = "visa_center_notes"
    __table_args__ = (
        UniqueConstraint("tenant_id", "visa_center_id", name="uq_visa_center_notes_center"),
        ForeignKeyConstraint(
            ["visa_center_id", "tenant_id"],
            ["visa_centers.id", "visa_centers.tenant_id"],
            ondelete="CASCADE",
            name="fk_visa_center_notes_visa_center_id_same_tenant",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    visa_center_id: Mapped[uuid.UUID]
    note: Mapped[str] = mapped_column(String(500))
    updated_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
