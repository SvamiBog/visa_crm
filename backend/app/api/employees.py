"""A specialist's employees: invite, choose modules, block, new password (backlog 0.7).
The cases an employee sees are given in the case itself (app/api/case_access.py); the
list shows how many each has. Letting one go (Т.15): the vault passwords they know, from
the audit log, and whether someone else changed each since (audit 2026-11, M2).

Everything runs in the specialist's own workspace scope (Row-Level Security)."""

import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import and_, delete, func, or_, select
from sqlalchemy.exc import IntegrityError

from app.api.deps import CurrentUser, TenantSession, effective_modules, require_specialist
from app.core.audit import record
from app.core.employee_limits import MAX_LIMIT
from app.core.modules import ModuleList
from app.core.security import generate_temporary_password, make_password_hash
from app.core.two_factor import reset_two_factor
from app.models import (
    AuditAction,
    AuditEvent,
    Contact,
    EmployeeCase,
    RecoveryCode,
    User,
    UserRole,
    UserSession,
    VaultEntry,
    VaultSystem,
)

router = APIRouter(prefix="/employees", tags=["employees"])

Specialist = Annotated[CurrentUser, Depends(require_specialist)]


class EmployeeIn(BaseModel):
    full_name: str = Field(min_length=1, max_length=200)
    email: str = Field(min_length=3, max_length=320, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    modules: ModuleList = []


DailyLimit = Annotated[int, Field(ge=1, le=MAX_LIMIT)]


class EmployeeUpdate(BaseModel):
    full_name: str | None = Field(default=None, min_length=1, max_length=200)
    modules: ModuleList | None = None
    # How many cards and passwords a day (app/core/employee_limits.py, Т.14).
    daily_card_limit: DailyLimit | None = None
    daily_password_limit: DailyLimit | None = None


class EmployeeOut(BaseModel):
    id: uuid.UUID
    full_name: str | None
    email: str
    modules: list[str]
    is_active: bool
    must_change_password: bool
    two_factor_enabled: bool
    last_login_at: datetime | None
    created_at: datetime
    # Cases the specialist gave them; their groups and what they created come on top.
    case_count: int
    daily_card_limit: int
    daily_password_limit: int


class RevealedPassword(BaseModel):
    """A vault password the employee knows: change it when they leave. They revealed it,
    or added the account (and so made it on the site) or typed its password."""

    entry_id: uuid.UUID
    system_name: str | None
    login: str | None  # None once the entry is deleted: the log holds no login
    contact_id: uuid.UUID | None
    contact_name: str | None
    times: int  # revealed; 0 for an account they added or a password they typed
    last_revealed_at: datetime  # the last time they saw or saved it
    # Someone else saved a new password after the employee last saw it: nothing more to
    # do. A password the employee saved, even the same one again, is one they know.
    changed_since: bool
    # The entry is gone but the account on the site is not: still to change.
    deleted: bool


# More than an employee ever reveals; bounds the answer.
REVEALED_LIMIT = 500
# A vault edit that saved a password (vault._audit lists the changed fields, sorted).
_PASSWORD_SAVED = AuditEvent.details["fields"].astext.regexp_match(r"(^|,)password(,|$)")


class EmployeeCreated(BaseModel):
    employee: EmployeeOut
    temporary_password: str


class TemporaryPassword(BaseModel):
    temporary_password: str


async def _case_counts(session: TenantSession, ids: list[uuid.UUID]) -> dict[uuid.UUID, int]:
    rows = await session.execute(
        select(EmployeeCase.user_id, func.count())
        .where(EmployeeCase.user_id.in_(ids))
        .group_by(EmployeeCase.user_id)
    )
    return dict(rows.all())


async def _out_one(session: TenantSession, user: User) -> EmployeeOut:
    return _out(user, (await _case_counts(session, [user.id])).get(user.id, 0))


def _out(user: User, case_count: int) -> EmployeeOut:
    return EmployeeOut(
        id=user.id,
        full_name=user.full_name,
        email=user.email,
        modules=user.modules,
        is_active=user.is_active,
        must_change_password=user.must_change_password,
        two_factor_enabled=user.totp_enabled_at is not None,
        last_login_at=user.last_login_at,
        created_at=user.created_at,
        case_count=case_count,
        daily_card_limit=user.daily_card_limit,
        daily_password_limit=user.daily_password_limit,
    )


async def _check_modules(session: TenantSession, specialist: CurrentUser, modules: list[str]):
    available = await effective_modules(session, specialist)
    if any(m not in available for m in modules):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "module_not_available")


async def _get_employee(session: TenantSession, specialist: CurrentUser, employee_id: uuid.UUID):
    employee = await session.scalar(
        select(User).where(
            User.id == employee_id,
            User.tenant_id == specialist.tenant_id,
            User.role == UserRole.EMPLOYEE,
        )
    )
    if employee is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not_found")
    return employee


def _record(
    session: TenantSession,
    action: AuditAction,
    specialist: CurrentUser,
    employee: User,
    details: dict[str, Any] | None = None,
) -> None:
    record(
        session,
        action,
        actor=specialist,
        target_type="user",
        target_id=employee.id,
        details={"email": employee.email, **(details or {})},
    )


@router.get("")
async def list_employees(specialist: Specialist, session: TenantSession) -> list[EmployeeOut]:
    employees = (
        await session.scalars(
            select(User)
            .where(User.tenant_id == specialist.tenant_id, User.role == UserRole.EMPLOYEE)
            .order_by(User.created_at)
        )
    ).all()
    counts = await _case_counts(session, [e.id for e in employees])
    return [_out(e, counts.get(e.id, 0)) for e in employees]


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_employee(
    body: EmployeeIn, specialist: Specialist, session: TenantSession
) -> EmployeeCreated:
    await _check_modules(session, specialist, body.modules)
    password = generate_temporary_password()
    employee = User(
        id=uuid.uuid4(),
        tenant_id=specialist.tenant_id,
        full_name=body.full_name.strip(),
        email=body.email.strip().lower(),
        password_hash=await make_password_hash(password),
        role=UserRole.EMPLOYEE,
        must_change_password=True,
        modules=body.modules,
    )
    session.add(employee)
    try:
        # Emails are unique across the platform; another workspace's users are invisible
        # here, so the database constraint is what catches a taken email.
        await session.flush()
    except IntegrityError:
        raise HTTPException(status.HTTP_409_CONFLICT, "email_taken") from None
    _record(session, AuditAction.EMPLOYEE_CREATED, specialist, employee)
    await session.commit()
    await session.refresh(employee)
    return EmployeeCreated(employee=_out(employee, 0), temporary_password=password)


@router.patch("/{employee_id}")
async def update_employee(
    employee_id: uuid.UUID, body: EmployeeUpdate, specialist: Specialist, session: TenantSession
) -> EmployeeOut:
    employee = await _get_employee(session, specialist, employee_id)
    changes = body.model_dump(exclude_unset=True, exclude_none=True)
    details: dict[str, Any] = {"changed": sorted(changes)}
    if body.modules is not None:
        await _check_modules(session, specialist, body.modules)
        details["modules_on"] = [m for m in body.modules if m not in employee.modules]
        details["modules_off"] = [m for m in employee.modules if m not in body.modules]
        employee.modules = body.modules
    if body.full_name is not None:
        employee.full_name = body.full_name.strip()
    if body.daily_card_limit is not None:
        employee.daily_card_limit = body.daily_card_limit
    if body.daily_password_limit is not None:
        employee.daily_password_limit = body.daily_password_limit
    if changes:
        _record(session, AuditAction.EMPLOYEE_UPDATED, specialist, employee, details)
    await session.commit()
    return await _out_one(session, employee)


async def _sign_out(session: TenantSession, employee: User) -> None:
    await session.execute(delete(UserSession).where(UserSession.user_id == employee.id))


@router.post("/{employee_id}/block")
async def block_employee(
    employee_id: uuid.UUID, specialist: Specialist, session: TenantSession
) -> EmployeeOut:
    employee = await _get_employee(session, specialist, employee_id)
    employee.is_active = False
    await _sign_out(session, employee)
    _record(session, AuditAction.EMPLOYEE_BLOCKED, specialist, employee)
    await session.commit()
    return await _out_one(session, employee)


@router.post("/{employee_id}/unblock")
async def unblock_employee(
    employee_id: uuid.UUID, specialist: Specialist, session: TenantSession
) -> EmployeeOut:
    employee = await _get_employee(session, specialist, employee_id)
    employee.is_active = True
    _record(session, AuditAction.EMPLOYEE_UNBLOCKED, specialist, employee)
    await session.commit()
    return await _out_one(session, employee)


@router.post("/{employee_id}/reset-password")
async def reset_employee_password(
    employee_id: uuid.UUID, specialist: Specialist, session: TenantSession
) -> TemporaryPassword:
    """New temporary password; also lifts a lockout, resets 2FA and signs the employee out."""
    employee = await _get_employee(session, specialist, employee_id)
    password = generate_temporary_password()
    employee.password_hash = await make_password_hash(password)
    employee.must_change_password = True
    employee.failed_login_count = 0
    employee.locked_until = None
    reset_two_factor(employee)
    await session.execute(delete(RecoveryCode).where(RecoveryCode.user_id == employee.id))
    await _sign_out(session, employee)
    _record(session, AuditAction.EMPLOYEE_PASSWORD_RESET, specialist, employee)
    await session.commit()
    return TemporaryPassword(temporary_password=password)


@router.get("/{employee_id}/revealed-passwords")
async def revealed_passwords(
    employee_id: uuid.UUID, specialist: Specialist, session: TenantSession
) -> list[RevealedPassword]:
    """Every vault password the employee knows, from the audit log, those still to change
    first. Neither the time a password was saved nor a deleted entry says it was changed:
    the employee can save the same password again or delete an account they added."""
    employee = await _get_employee(session, specialist, employee_id)
    knows = or_(
        AuditEvent.action.in_(
            [AuditAction.VAULT_PASSWORD_REVEALED, AuditAction.VAULT_ENTRY_CREATED]
        ),
        and_(AuditEvent.action == AuditAction.VAULT_ENTRY_UPDATED, _PASSWORD_SAVED),
    )
    last = func.max(AuditEvent.occurred_at)
    known = (
        await session.execute(
            select(
                AuditEvent.target_id,
                func.count().filter(AuditEvent.action == AuditAction.VAULT_PASSWORD_REVEALED),
                last,
                func.max(AuditEvent.details["name"].astext),
                func.max(AuditEvent.details["contact"].astext),
            )
            .where(AuditEvent.actor_id == employee.id, AuditEvent.target_id.is_not(None), knows)
            .group_by(AuditEvent.target_id)
            .order_by(last.desc())
            .limit(REVEALED_LIMIT)
        )
    ).all()
    targets = [target for target, *_ in known]
    # The last password someone else saved: a new one the employee has not seen.
    changed = dict(
        (
            await session.execute(
                select(AuditEvent.target_id, func.max(AuditEvent.occurred_at))
                .where(
                    AuditEvent.action == AuditAction.VAULT_ENTRY_UPDATED,
                    AuditEvent.target_id.in_(targets),
                    AuditEvent.actor_id.is_distinct_from(employee.id),
                    _PASSWORD_SAVED,
                )
                .group_by(AuditEvent.target_id)
            )
        ).all()
    )
    entries = {
        entry.id: (entry, system)
        for entry, system in (
            await session.execute(
                select(VaultEntry, VaultSystem.name)
                .join(VaultSystem, VaultSystem.id == VaultEntry.system_id)
                .where(VaultEntry.id.in_([uuid.UUID(t) for t in targets]))
            )
        )
    }
    # Whose account it is: from the entry, or from the log once it is deleted (an account
    # never changes its owner). The log has no login, and it must not get one.
    owners: dict[str, uuid.UUID | None] = {}
    for target, *_, contact in known:
        found = entries.get(uuid.UUID(target))
        logged = uuid.UUID(contact) if contact else None
        owners[target] = found[0].contact_id if found else logged
    names = dict(
        (
            await session.execute(
                select(Contact.id, Contact.full_name).where(
                    Contact.id.in_([c for c in owners.values() if c])
                )
            )
        ).all()
    )
    rows = []
    for target, times, last_at, logged_system, _ in known:
        entry, system_name = entries.get(uuid.UUID(target), (None, logged_system))
        owner = owners[target] if owners[target] in names else None
        # Taking the password out of the vault leaves the one on the site as it was.
        cleared = entry is not None and entry.password_encrypted is None
        rows.append(
            RevealedPassword(
                entry_id=uuid.UUID(target),
                system_name=system_name,
                login=entry.login if entry else None,
                contact_id=owner,
                contact_name=names.get(owner),
                times=times,
                last_revealed_at=last_at,
                changed_since=target in changed and changed[target] > last_at and not cleared,
                deleted=entry is None,
            )
        )
    # Still to change first; a deleted entry is too, the account on the site is still there.
    rows.sort(key=lambda r: r.changed_since)
    return rows
