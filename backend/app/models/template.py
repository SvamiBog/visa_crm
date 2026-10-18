"""Document templates (шаблоны документов, backlog 2.1): a specialist's own Word files with
codes (app/core/templates.py) and the templates given to a client.

The filled file is made on every download from the data of that moment and is never
stored: only the blank template lives in the file storage, encrypted with the workspace's
key, under templates/<tenant>/<id> (app/core/documents.py, app/core/storage.py).
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


def _same_tenant(column: str, parent: str, ondelete: str) -> ForeignKeyConstraint:
    return ForeignKeyConstraint(
        [column, "tenant_id"],
        [f"{parent}.id", f"{parent}.tenant_id"],
        ondelete=ondelete,
        name=f"fk_contact_templates_{column}_same_tenant",
    )


class DocumentTemplate(Base):
    """A Word file of the specialist's with codes where the client's data goes. Switched
    off, it is no longer offered for new clients; the clients who have it keep it."""

    __tablename__ = "document_templates"
    __table_args__ = (UniqueConstraint("id", "tenant_id", name="uq_document_templates_id_tenant"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(200))
    # How the filled file is named: text with codes; NULL — «<name> — <client>».
    file_name: Mapped[str | None] = mapped_column(String(200))
    # The uploaded file's own name: the blank template is downloaded with it.
    source_name: Mapped[str] = mapped_column(String(200))
    size_bytes: Mapped[int] = mapped_column(Integer)
    # Found in the file: the keys of the known codes, and the unknown ones as written.
    codes: Mapped[list[str]] = mapped_column(ARRAY(String(40)), server_default=text("'{}'"))
    unknown_codes: Mapped[list[str]] = mapped_column(
        ARRAY(String(100)), server_default=text("'{}'")
    )
    is_active: Mapped[bool] = mapped_column(server_default=text("true"))
    uploaded_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ContactTemplate(Base):
    """A template given to a client: for one of their cases (its codes come from it), or
    for the client as a whole. Goes when the template is deleted."""

    __tablename__ = "contact_templates"
    __table_args__ = (
        _same_tenant("contact_id", "contacts", "CASCADE"),
        _same_tenant("template_id", "document_templates", "CASCADE"),
        _same_tenant("case_id", "visa_cases", "SET NULL (case_id)"),
        Index("ix_contact_templates_contact_id", "contact_id"),
        Index("ix_contact_templates_template_id", "template_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    contact_id: Mapped[uuid.UUID]
    template_id: Mapped[uuid.UUID]
    case_id: Mapped[uuid.UUID | None]
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
