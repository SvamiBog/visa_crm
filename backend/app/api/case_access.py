"""Which employees see a case (вопрос 17, docs/decisions.md → «Сотрудники: только свои
кейсы»). The specialist ticks employees in the case; an employee then sees that case, the
rest of its submission group and the client of each, besides the clients and cases they
created. Row-Level Security does the hiding (migration 0031); this router only keeps the
grants. Events carry ids only.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import delete, select

from app.api.deps import CurrentUser, TenantSession, require_module, require_specialist
from app.core.audit import record
from app.models import AuditAction, EmployeeCase, User, UserRole, VisaCase

router = APIRouter(
    tags=["cases"],
    dependencies=[Depends(require_module("clients")), Depends(require_module("cases"))],
)

Specialist = Annotated[CurrentUser, Depends(require_specialist)]

# More than any workspace has employees; bounds the request.
MAX_EMPLOYEES = 100


class CaseEmployee(BaseModel):
    id: uuid.UUID
    full_name: str | None
    email: str
    is_active: bool
    # Without «Клиенты» and «Кейсы» the employee cannot open the case even when given it.
    has_modules: bool
    granted: bool


class CaseEmployeesIn(BaseModel):
    employee_ids: Annotated[list[uuid.UUID], Field(max_length=MAX_EMPLOYEES)]


async def _case(session: TenantSession, case_id: uuid.UUID) -> VisaCase:
    case = await session.get(VisaCase, case_id)
    if case is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not_found")
    return case


async def _employees(session: TenantSession, case: VisaCase) -> list[CaseEmployee]:
    granted = set(
        await session.scalars(select(EmployeeCase.user_id).where(EmployeeCase.case_id == case.id))
    )
    employees = await session.scalars(
        select(User).where(User.role == UserRole.EMPLOYEE).order_by(User.created_at)
    )
    return [
        CaseEmployee(
            id=e.id,
            full_name=e.full_name,
            email=e.email,
            is_active=e.is_active,
            has_modules={"clients", "cases"} <= set(e.modules),
            granted=e.id in granted,
        )
        for e in employees
    ]


@router.get("/cases/{case_id}/employees")
async def case_employees(
    case_id: uuid.UUID, specialist: Specialist, session: TenantSession
) -> list[CaseEmployee]:
    return await _employees(session, await _case(session, case_id))


@router.put("/cases/{case_id}/employees")
async def set_case_employees(
    case_id: uuid.UUID, body: CaseEmployeesIn, specialist: Specialist, session: TenantSession
) -> list[CaseEmployee]:
    """Replace the list of employees who see the case."""
    case = await _case(session, case_id)
    wanted = set(body.employee_ids)
    # Users of other workspaces are hidden by Row-Level Security, so they are unknown too.
    known = set(
        await session.scalars(
            select(User.id).where(User.id.in_(wanted), User.role == UserRole.EMPLOYEE)
        )
    )
    if known != wanted:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "unknown_employee")
    current = set(
        await session.scalars(select(EmployeeCase.user_id).where(EmployeeCase.case_id == case.id))
    )
    granted, revoked = sorted(wanted - current), sorted(current - wanted)
    if revoked:
        await session.execute(
            delete(EmployeeCase).where(
                EmployeeCase.case_id == case.id, EmployeeCase.user_id.in_(revoked)
            )
        )
    session.add_all(
        EmployeeCase(
            user_id=user_id, case_id=case.id, tenant_id=case.tenant_id, granted_by=specialist.id
        )
        for user_id in granted
    )
    if granted or revoked:
        record(
            session,
            AuditAction.CASE_ACCESS_UPDATED,
            actor=specialist,
            target_type="case",
            target_id=case.id,
            details={"granted": [str(u) for u in granted], "revoked": [str(u) for u in revoked]},
        )
    await session.commit()
    return await _employees(session, case)
