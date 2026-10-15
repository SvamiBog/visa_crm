from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select

from app.api.deps import CurrentUser, TenantSession, require_tenant_user
from app.models import Tenant

router = APIRouter(tags=["workspace"])


class WorkspaceOut(BaseModel):
    name: str
    base_currency: str
    timezone: str


@router.get("/workspace")
async def workspace(
    user: Annotated[CurrentUser, Depends(require_tenant_user)], session: TenantSession
) -> WorkspaceOut:
    """The specialist's workspace shown in the cabinet header."""
    tenant = await session.scalar(select(Tenant).where(Tenant.id == user.tenant_id))
    assert tenant is not None  # the session dependency already checked the tenant is active
    return WorkspaceOut(
        name=tenant.name, base_currency=tenant.base_currency, timezone=tenant.timezone
    )
