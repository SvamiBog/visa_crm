"""A specialist's employees: invite, choose modules, block, new password (backlog 0.7).
The cases an employee sees are given in the case itself (app/api/case_access.py); the
list shows how many each has.

Everything runs in the specialist's own workspace scope (Row-Level Security)."""

import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError

from app.api.deps import CurrentUser, TenantSession, effective_modules, require_specialist
from app.core.audit import record
from app.core.employee_limits import MAX_LIMIT
from app.core.modules import ModuleList
from app.core.security import generate_temporary_password, hash_password
from app.core.two_factor import reset_two_factor
from app.models import AuditAction, EmployeeCase, RecoveryCode, User, UserRole, UserSession

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
    return dict(rows.tuples().all())


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
        password_hash=hash_password(password),
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
    employee.password_hash = hash_password(password)
    employee.must_change_password = True
    employee.failed_login_count = 0
    employee.locked_until = None
    reset_two_factor(employee)
    await session.execute(delete(RecoveryCode).where(RecoveryCode.user_id == employee.id))
    await _sign_out(session, employee)
    _record(session, AuditAction.EMPLOYEE_PASSWORD_RESET, specialist, employee)
    await session.commit()
    return TemporaryPassword(temporary_password=password)
