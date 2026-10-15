from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select

from app.api.deps import (
    CurrentUser,
    TenantSession,
    current_user,
    effective_modules,
    require_tenant_user,
)
from app.core.modules import MODULES
from app.models import Tenant

router = APIRouter(tags=["workspace"])


class WorkspaceOut(BaseModel):
    name: str
    base_currency: str
    tax_rate: Decimal
    timezone: str
    # What this user may open: the menu shows these.
    modules: list[str]


class ModuleOut(BaseModel):
    key: str
    block: str
    stage: int


@router.get("/workspace")
async def workspace(
    user: Annotated[CurrentUser, Depends(require_tenant_user)], session: TenantSession
) -> WorkspaceOut:
    """The specialist's workspace shown in the cabinet header."""
    tenant = await session.scalar(select(Tenant).where(Tenant.id == user.tenant_id))
    assert tenant is not None  # the session dependency already checked the tenant is active
    return WorkspaceOut(
        name=tenant.name,
        base_currency=tenant.base_currency,
        tax_rate=tenant.tax_rate,
        timezone=tenant.timezone,
        modules=await effective_modules(session, user),
    )


@router.get("/modules", dependencies=[Depends(current_user)])
async def modules() -> list[ModuleOut]:
    """All product modules in menu order (names are translated in the UI)."""
    return [ModuleOut(key=m.key, block=m.block, stage=m.stage) for m in MODULES]
