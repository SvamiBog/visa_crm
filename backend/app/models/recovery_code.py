"""Recovery codes of two-factor login (backlog 0.5): one-time codes for signing in without
the authenticator app, kept only as hashes (app/core/two_factor.py). A workspace sees and
removes only the codes of its own users (migration 0007); only the system scope writes
them (migration 0043).
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class RecoveryCode(Base):
    """One-time code for logging in without the authenticator app. Stored as SHA-256."""

    __tablename__ = "user_recovery_codes"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    code_hash: Mapped[str] = mapped_column(String(64))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
