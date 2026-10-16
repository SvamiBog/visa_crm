"""Bookings of a case (backlog 1.8): hotels with their free cancellation deadline, the
client's flight booking with its validity, and the «Брони: дедлайны» view of all live
bookings by the nearest deadline.

Closed by the «clients» and «cases» modules. The account a booking was made in is only
referred to by id: its login stays in the vault.
"""

import uuid
from datetime import date
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, TenantSession, require_module, workspace_today
from app.core.audit import record
from app.core.bookings import LIVE, BookingWarning, days_left, deadline, warnings
from app.models import (
    AuditAction,
    Booking,
    BookingKind,
    BookingStatus,
    CaseStage,
    Contact,
    VaultEntry,
    VisaCase,
)

router = APIRouter(
    tags=["bookings"],
    dependencies=[Depends(require_module("clients")), Depends(require_module("cases"))],
)

Member = Annotated[CurrentUser, Depends(require_module("cases"))]

DEADLINES_LIMIT = 500


# --- Schemas -------------------------------------------------------------------------


def _strip(value: object) -> object:
    return value.strip() if isinstance(value, str) else value


def _blank_is_none(value: object) -> object:
    return (value.strip() or None) if isinstance(value, str) else value


class BookingIn(BaseModel):
    kind: BookingKind
    place: Annotated[str, Field(min_length=1, max_length=200)]
    starts_on: date | None = None
    ends_on: date | None = None
    reference: Annotated[str, Field(max_length=64)] | None = None
    free_cancel_until: date | None = None
    valid_until: date | None = None
    vault_entry_id: uuid.UUID | None = None
    note: Annotated[str, Field(max_length=500)] | None = None

    _place = field_validator("place", mode="before")(_strip)
    _optional = field_validator("reference", "note", mode="before")(_blank_is_none)


class BookingUpdate(BaseModel):
    """Only the fields sent are changed; null clears an optional field."""

    kind: BookingKind | None = None
    status: BookingStatus | None = None
    place: Annotated[str, Field(min_length=1, max_length=200)] | None = None
    starts_on: date | None = None
    ends_on: date | None = None
    reference: Annotated[str, Field(max_length=64)] | None = None
    free_cancel_until: date | None = None
    valid_until: date | None = None
    vault_entry_id: uuid.UUID | None = None
    note: Annotated[str, Field(max_length=500)] | None = None

    _place = field_validator("place", mode="before")(_strip)
    _optional = field_validator("reference", "note", mode="before")(_blank_is_none)


class BookingOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    case_id: uuid.UUID
    kind: BookingKind
    status: BookingStatus
    place: str
    starts_on: date | None
    ends_on: date | None
    reference: str | None
    free_cancel_until: date | None
    valid_until: date | None
    vault_entry_id: uuid.UUID | None
    note: str | None
    # The cancellation date of a hotel or the validity of a flight booking.
    deadline: date | None = None
    days_left: int | None = None
    warnings: list[BookingWarning] = []


class DeadlineRow(BookingOut):
    """A live booking with its client and case, for «Брони: дедлайны»."""

    contact_id: uuid.UUID
    contact_name: str
    case_number: int
    destination_id: uuid.UUID
    stage: CaseStage


# --- Helpers -------------------------------------------------------------------------


def booking_out(booking: Booking, case: VisaCase, today: date) -> BookingOut:
    out = BookingOut.model_validate(booking)
    out.deadline = deadline(booking.kind, booking.free_cancel_until, booking.valid_until)
    out.days_left = days_left(out.deadline, today)
    out.warnings = warnings(
        kind=booking.kind,
        status=booking.status,
        free_cancel_until=booking.free_cancel_until,
        valid_until=booking.valid_until,
        stage=case.stage,
        submission_on=case.submission_on,
        today=today,
    )
    return out


async def bookings_of(
    session: AsyncSession, cases: list[VisaCase], today: date
) -> dict[uuid.UUID, list[BookingOut]]:
    """Each case's bookings: live ones first, then by their dates."""
    by_id = {c.id: c for c in cases}
    result: dict[uuid.UUID, list[BookingOut]] = {i: [] for i in by_id}
    if not by_id:
        return result
    rows = await session.scalars(
        select(Booking)
        .where(Booking.case_id.in_(by_id))
        .order_by(Booking.starts_on.nulls_last(), Booking.created_at)
    )
    for booking in rows:
        result[booking.case_id].append(booking_out(booking, by_id[booking.case_id], today))
    for items in result.values():
        items.sort(key=lambda b: b.status not in LIVE)
    return result


async def _get[T](session: AsyncSession, model: type[T], item_id: uuid.UUID) -> T:
    """RLS hides other workspaces' rows, so someone else's id is simply not found."""
    item = await session.get(model, item_id)
    if item is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not_found")
    return item


async def _check(session: AsyncSession, booking: Booking, case: VisaCase) -> None:
    """The dates fit the kind, and the account is the workspace's own or the client's."""
    if booking.kind == BookingKind.HOTEL and booking.valid_until is not None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "booking_dates_kind")
    if booking.kind == BookingKind.FLIGHT and booking.free_cancel_until is not None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "booking_dates_kind")
    if booking.starts_on and booking.ends_on and booking.ends_on < booking.starts_on:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "dates_order")
    if booking.vault_entry_id is not None:
        account = await _get(session, VaultEntry, booking.vault_entry_id)
        if account.contact_id not in (None, case.contact_id):
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "account_of_another_contact")


def _audit(
    session: AsyncSession,
    action: AuditAction,
    user: CurrentUser,
    booking: Booking,
    case: VisaCase,
    details: dict[str, Any] | None = None,
) -> None:
    """Ids and field names only: no hotel, dates or booking number."""
    record(
        session,
        action,
        actor=user,
        target_type="booking",
        target_id=booking.id,
        details={"case": str(case.id), "contact": str(case.contact_id), **(details or {})},
    )


# --- Endpoints -----------------------------------------------------------------------


@router.post("/cases/{case_id}/bookings", status_code=status.HTTP_201_CREATED)
async def add_booking(
    case_id: uuid.UUID, body: BookingIn, user: Member, session: TenantSession
) -> BookingOut:
    case = await _get(session, VisaCase, case_id)
    booking = Booking(
        tenant_id=user.tenant_id, case_id=case.id, created_by=user.id, **body.model_dump()
    )
    await _check(session, booking, case)
    session.add(booking)
    await session.flush()
    _audit(session, AuditAction.BOOKING_ADDED, user, booking, case, {"kind": booking.kind.value})
    await session.commit()
    await session.refresh(booking)
    assert user.tenant_id is not None
    return booking_out(booking, case, await workspace_today(session, user.tenant_id))


@router.patch("/bookings/{booking_id}")
async def update_booking(
    booking_id: uuid.UUID, body: BookingUpdate, user: Member, session: TenantSession
) -> BookingOut:
    booking = await _get(session, Booking, booking_id)
    case = await _get(session, VisaCase, booking.case_id)
    changes = body.model_dump(exclude_unset=True)
    for field in ("kind", "status", "place"):
        if field in changes and changes[field] is None:
            del changes[field]  # required: null means «leave as is»
    changed = sorted(f for f, v in changes.items() if getattr(booking, f) != v)
    for field, value in changes.items():
        setattr(booking, field, value)
    await _check(session, booking, case)
    if changed:
        _audit(
            session, AuditAction.BOOKING_UPDATED, user, booking, case, {"fields": ",".join(changed)}
        )
    await session.commit()
    await session.refresh(booking)
    assert user.tenant_id is not None
    return booking_out(booking, case, await workspace_today(session, user.tenant_id))


@router.delete("/bookings/{booking_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_booking(booking_id: uuid.UUID, user: Member, session: TenantSession) -> None:
    """For a booking entered by mistake; a cancelled booking is kept as «отменена»."""
    booking = await _get(session, Booking, booking_id)
    case = await _get(session, VisaCase, booking.case_id)
    _audit(session, AuditAction.BOOKING_DELETED, user, booking, case)
    await session.delete(booking)
    await session.commit()


@router.get("/bookings/deadlines")
async def booking_deadlines(
    user: Member,
    session: TenantSession,
    country_id: Annotated[uuid.UUID | None, Query()] = None,
) -> list[DeadlineRow]:
    """Live bookings of all clients whose dates have not passed, nearest deadline first."""
    assert user.tenant_id is not None
    today = await workspace_today(session, user.tenant_id)
    due = func.coalesce(Booking.free_cancel_until, Booking.valid_until)
    query = (
        select(Booking, VisaCase, Contact.full_name)
        .join(VisaCase, VisaCase.id == Booking.case_id)
        .join(Contact, Contact.id == VisaCase.contact_id)
        .where(
            Booking.status.in_(LIVE),
            (Booking.ends_on.is_(None)) | (Booking.ends_on >= today),
        )
        .order_by(due.nulls_last(), Booking.starts_on.nulls_last(), Booking.created_at)
        .limit(DEADLINES_LIMIT)
    )
    if country_id is not None:
        query = query.where(VisaCase.destination_id == country_id)
    rows = await session.execute(query)
    return [
        DeadlineRow(
            **booking_out(booking, case, today).model_dump(),
            contact_id=case.contact_id,
            contact_name=name,
            case_number=case.number,
            destination_id=case.destination_id,
            stage=case.stage,
        )
        for booking, case, name in rows.tuples()
    ]
