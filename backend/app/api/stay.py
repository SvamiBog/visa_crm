"""The 90/180 calculator (backlog 2.6): a client's trips in the card and the count of
days in the Schengen area (app/core/stay.py).

Closed by the «stay_calculator» module; the trips of a card also need «clients». The
calculation takes any list of trips, saved or not, so the calculator page works for a
person who is not a client yet. Trips go to the audit log by id only.
"""

import uuid
from datetime import date, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, TenantSession, require_module, workspace_today
from app.core import stay
from app.core.audit import record
from app.models import AuditAction, Contact, Country, Trip

router = APIRouter(tags=["stay"], dependencies=[Depends(require_module("stay_calculator"))])

Member = Annotated[CurrentUser, Depends(require_module("stay_calculator"))]
CardMember = Annotated[CurrentUser, Depends(require_module("clients"))]

# Trips older than that are of no use, and later ones are not planned yet.
FIRST_DAY = date(1990, 1, 1)
DAYS_AHEAD = 730
# A short stay is never that long: a longer one is under a long-stay visa or a permit.
MAX_TRIP_DAYS = 366
MAX_TRIPS = 200

Day = Annotated[date, Field(ge=FIRST_DAY)]


# --- Schemas -------------------------------------------------------------------------


def _blank_is_none(value: object) -> object:
    return (value.strip() or None) if isinstance(value, str) else value


class StayTrip(BaseModel):
    """A trip to count: where, when, and whether under a long-stay visa or a permit."""

    country_id: uuid.UUID
    entered_on: Day
    left_on: Day | None = None
    long_stay: bool = False


class TripIn(StayTrip):
    note: Annotated[str, Field(max_length=200)] | None = None

    _note = field_validator("note", mode="before")(_blank_is_none)


class TripUpdate(BaseModel):
    """Only the fields sent are changed; null clears the exit date or the note."""

    country_id: uuid.UUID | None = None
    entered_on: Day | None = None
    left_on: Day | None = None
    long_stay: bool | None = None
    note: Annotated[str, Field(max_length=200)] | None = None

    _note = field_validator("note", mode="before")(_blank_is_none)


class TripOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    country_id: uuid.UUID
    entered_on: date
    left_on: date | None
    long_stay: bool
    note: str | None


class PlannedTrip(BaseModel):
    entered_on: Day
    left_on: Day | None = None


class StayRequest(BaseModel):
    trips: Annotated[list[StayTrip], Field(max_length=MAX_TRIPS)] = []
    # A trip to check against the ones listed, without saving it.
    check: PlannedTrip | None = None


class PeriodOut(BaseModel):
    first: date
    last: date


class SummaryOut(BaseModel):
    used: int
    in_area: bool
    stay_until: date | None
    next_entry: date
    full_stay_from: date
    overstays: list[PeriodOut]


class TripResultOut(BaseModel):
    """The count for one trip, in the order the trips were given."""

    counts: bool
    days: int
    used_at_end: int | None
    overstay_from: date | None


class CheckOut(BaseModel):
    length: int | None
    used_before: int
    stay_until: date | None
    fits: bool | None
    earliest_entry: date | None


class StayOut(BaseModel):
    today: date
    max_days: int = stay.MAX_DAYS
    window_days: int = stay.WINDOW_DAYS
    summary: SummaryOut
    trips: list[TripResultOut]
    check: CheckOut | None = None


class ContactTripsOut(BaseModel):
    """The card's trips, latest first, with the count for each (in the same order)."""

    trips: list[TripOut]
    stay: StayOut


# --- Helpers -------------------------------------------------------------------------


def _check_dates(
    entered_on: date, left_on: date | None, long_stay: bool, today: date, *, check: bool = False
) -> None:
    """A trip to check may have no exit date yet: the answer is then how long it can last."""
    if left_on is not None and left_on < entered_on:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "dates_order")
    if max(entered_on, left_on or entered_on) > today + timedelta(days=DAYS_AHEAD):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "trip_too_far")
    if left_on is None and entered_on > today and not check:
        # Still there, but not there yet: a planned trip needs its exit date.
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "trip_exit_required")
    if not long_stay and ((left_on or today) - entered_on).days + 1 > MAX_TRIP_DAYS:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "trip_too_long")


async def _schengen_since(
    session: AsyncSession, country_ids: set[uuid.UUID]
) -> dict[uuid.UUID, date | None]:
    if not country_ids:
        return {}
    rows = await session.execute(
        select(Country.id, Country.schengen_since).where(Country.id.in_(country_ids))
    )
    found = dict(rows.all())
    if len(found) != len(country_ids):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "unknown_country")
    return found


async def calculate(
    session: AsyncSession,
    trips: list[StayTrip] | list[Trip],
    today: date,
    planned: PlannedTrip | None = None,
) -> StayOut:
    since = await _schengen_since(session, {t.country_id for t in trips})
    counted = [
        stay.Trip(
            entered_on=t.entered_on,
            left_on=t.left_on,
            counts_from=None if t.long_stay else since[t.country_id],
        )
        for t in trips
    ]
    days = stay.Days.of(counted, today)
    summary = stay.summary(days, today)
    out = StayOut(
        today=today,
        summary=SummaryOut(
            used=summary.used,
            in_area=summary.in_area,
            stay_until=summary.stay_until,
            next_entry=summary.next_entry,
            full_stay_from=summary.full_stay_from,
            overstays=[PeriodOut(first=p.first, last=p.last) for p in summary.overstays],
        ),
        trips=[
            TripResultOut(
                # A trip before its country joined the area counts no days either.
                counts=r.days > 0,
                days=r.days,
                used_at_end=r.used_at_end,
                overstay_from=r.overstay_from,
            )
            for r in stay.trip_results(counted, days, today)
        ],
    )
    if planned is not None:
        result = stay.check(days, planned.entered_on, planned.left_on, today)
        out.check = CheckOut(
            length=result.length,
            used_before=result.used_before,
            stay_until=result.stay_until,
            fits=result.fits,
            earliest_entry=result.earliest_entry,
        )
    return out


async def _today(session: AsyncSession, user: CurrentUser) -> date:
    assert user.tenant_id is not None
    return await workspace_today(session, user.tenant_id)


async def _trip(session: AsyncSession, trip_id: uuid.UUID) -> Trip:
    """RLS hides other workspaces' rows, so their ids are simply not found."""
    trip = await session.get(Trip, trip_id)
    if trip is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not_found")
    return trip


def _audit(
    session: AsyncSession,
    action: AuditAction,
    user: CurrentUser,
    trip: Trip,
    details: dict[str, Any] | None = None,
) -> None:
    """Ids and field names only: no countries or dates."""
    record(
        session,
        action,
        actor=user,
        target_type="contact",
        target_id=trip.contact_id,
        details={"trip": str(trip.id), **(details or {})},
    )


# --- Endpoints -----------------------------------------------------------------------


@router.post("/stay/calculate")
async def calculate_stay(body: StayRequest, user: Member, session: TenantSession) -> StayOut:
    """Count any trips, saved or not, and check a planned one. Nothing is stored."""
    today = await _today(session, user)
    for trip in body.trips:
        _check_dates(trip.entered_on, trip.left_on, trip.long_stay, today)
    if body.check is not None:
        _check_dates(body.check.entered_on, body.check.left_on, False, today, check=True)
    return await calculate(session, body.trips, today, body.check)


@router.get("/contacts/{contact_id}/trips")
async def contact_trips(
    contact_id: uuid.UUID, user: Member, _card: CardMember, session: TenantSession
) -> ContactTripsOut:
    if await session.get(Contact, contact_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not_found")
    trips = list(
        await session.scalars(
            select(Trip)
            .where(Trip.contact_id == contact_id)
            .order_by(Trip.entered_on.desc(), Trip.created_at.desc())
        )
    )
    return ContactTripsOut(
        trips=[TripOut.model_validate(t) for t in trips],
        stay=await calculate(session, trips, await _today(session, user)),
    )


@router.post("/contacts/{contact_id}/trips", status_code=status.HTTP_201_CREATED)
async def add_trip(
    contact_id: uuid.UUID, body: TripIn, user: Member, _card: CardMember, session: TenantSession
) -> TripOut:
    if await session.get(Contact, contact_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not_found")
    today = await _today(session, user)
    _check_dates(body.entered_on, body.left_on, body.long_stay, today)
    await _schengen_since(session, {body.country_id})
    trip = Trip(
        tenant_id=user.tenant_id, contact_id=contact_id, created_by=user.id, **body.model_dump()
    )
    session.add(trip)
    await session.flush()
    _audit(session, AuditAction.TRIP_ADDED, user, trip)
    await session.commit()
    await session.refresh(trip)
    return TripOut.model_validate(trip)


@router.patch("/trips/{trip_id}")
async def update_trip(
    trip_id: uuid.UUID, body: TripUpdate, user: Member, _card: CardMember, session: TenantSession
) -> TripOut:
    trip = await _trip(session, trip_id)
    changes = body.model_dump(exclude_unset=True)
    for field in ("country_id", "entered_on", "long_stay"):
        if field in changes and changes[field] is None:
            del changes[field]  # required: null means «leave as is»
    changed = sorted(f for f, v in changes.items() if getattr(trip, f) != v)
    for field, value in changes.items():
        setattr(trip, field, value)
    today = await _today(session, user)
    _check_dates(trip.entered_on, trip.left_on, trip.long_stay, today)
    if "country_id" in changed:
        await _schengen_since(session, {trip.country_id})
    if changed:
        _audit(session, AuditAction.TRIP_UPDATED, user, trip, {"fields": ",".join(changed)})
    await session.commit()
    await session.refresh(trip)
    return TripOut.model_validate(trip)


@router.delete("/trips/{trip_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_trip(
    trip_id: uuid.UUID, user: Member, _card: CardMember, session: TenantSession
) -> None:
    trip = await _trip(session, trip_id)
    _audit(session, AuditAction.TRIP_DELETED, user, trip)
    await session.delete(trip)
    await session.commit()
