"""A new login email waiting for the code sent to it (account settings).

One per user: asking again replaces it. Only the login flow (system scope) reads or
writes these rows, like sessions and recovery codes.
"""

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, SmallInteger, String, text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class EmailChange(Base):
    __tablename__ = "email_changes"
    __table_args__ = (CheckConstraint("new_email = lower(new_email)", name="new_email_lowercase"),)

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    new_email: Mapped[str] = mapped_column(String(320))
    # argon2 of the 6-digit code: a leaked row does not give the code away.
    code_hash: Mapped[str] = mapped_column(String(255))
    attempts: Mapped[int] = mapped_column(SmallInteger, server_default=text("0"))
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
