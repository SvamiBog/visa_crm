"""What the specialist marked «Готово» in «Требует внимания» (app/core/reminders.py).

A reminder is computed, not stored; a dismissal hides one occurrence of it by its key
(kind, subject id, trigger day) for the whole workspace.
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class ReminderDismissal(Base):
    __tablename__ = "reminder_dismissals"
    __table_args__ = (UniqueConstraint("tenant_id", "key", name="uq_reminder_dismissals_key"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    key: Mapped[str] = mapped_column(String(120))
    dismissed_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    dismissed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
