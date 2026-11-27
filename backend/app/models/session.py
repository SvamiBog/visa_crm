"""Login sessions (backlog 0.4): one row per signed-in browser, found by the hash of the
cookie's token (app/api/deps.py). A workspace sees only the sessions of its own users
(migration 0007) — that is how a specialist signs an employee out; only the system scope
writes them (migration 0043).
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class UserSession(Base):
    """Server-side login session. The cookie holds the token; only its SHA-256 is stored."""

    __tablename__ = "user_sessions"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # Password checked, second factor not yet: the session can only finish the login.
    mfa_pending: Mapped[bool] = mapped_column(server_default=text("false"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
