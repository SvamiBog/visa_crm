import logging
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.api.auth import check_current_password, reject_wrong_secret
from app.api.deps import (
    CurrentUser,
    SystemSession,
    TenantSession,
    current_user,
    effective_modules,
    require_specialist,
    require_tenant_user,
)
from app.api.subscriptions import SubscriptionState, subscription_state
from app.core import import_jobs, import_uploads, storage
from app.core.audit import record
from app.core.erase import erase_workspace
from app.core.modules import MODULES
from app.core.security import MAX_PASSWORD_LENGTH
from app.core.two_factor import looks_like_recovery_code, unseal_secret, verify_totp
from app.models import AuditAction, Tenant, User, UserRole

logger = logging.getLogger(__name__)

router = APIRouter(tags=["workspace"])


class WorkspaceOut(BaseModel):
    name: str
    base_currency: str
    tax_rate: Decimal
    timezone: str
    # What this user may open: the menu shows these.
    modules: list[str]
    # Files (consents, contracts) can be attached: a file storage is set (app/core/storage.py).
    file_storage: bool
    # The CRM subscription's state, for the specialist only (a notice when overdue).
    subscription: SubscriptionState | None


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
        file_storage=storage.is_configured(),
        subscription=await subscription_state(session, tenant)
        if user.role == UserRole.SPECIALIST
        else None,
    )


@router.get("/modules", dependencies=[Depends(current_user)])
async def modules() -> list[ModuleOut]:
    """All product modules in menu order (names are translated in the UI)."""
    return [ModuleOut(key=m.key, block=m.block, stage=m.stage) for m in MODULES]


class EraseIn(BaseModel):
    password: str = Field(min_length=1, max_length=MAX_PASSWORD_LENGTH)
    # A current code from the authenticator app; recovery codes do not erase.
    code: str = Field(min_length=6, max_length=32)


@router.post("/workspace/erase", status_code=status.HTTP_204_NO_CONTENT)
async def erase(
    body: EraseIn,
    user: Annotated[CurrentUser, Depends(require_specialist)],
    system: SystemSession,
    session: TenantSession,
) -> Response:
    """Delete all the workspace's data for good: the specialist moves to another system
    (docs/decisions.md → «Удаление всех данных кабинета»). Only with two-factor login on,
    confirmed with the password and a code from the app, so neither a stolen session nor a
    known password alone can do it; misses count with wrong logins. The account, its
    settings, the subscription and the audit log stay (app/core/erase.py)."""
    assert user.tenant_id is not None
    owner = await system.get_one(User, user.id, with_for_update=True)
    if owner.totp_enabled_at is None or owner.totp_secret_encrypted is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "two_factor_required")
    # An import writing cards now would put them back after the erasure.
    if any(job.kind == "import" for job in import_jobs.running(user.tenant_id)):
        raise HTTPException(status.HTTP_409_CONFLICT, "import_running")
    await check_current_password(system, owner, body.password, "erase_password")
    step = (
        None
        if looks_like_recovery_code(body.code)
        else verify_totp(
            unseal_secret(owner.id, owner.totp_secret_encrypted), body.code, owner.totp_last_step
        )
    )
    if step is None:
        await reject_wrong_secret(system, owner, "erase_code", "invalid_code")
    owner.totp_last_step = step
    await system.commit()

    # A sample or a check still reading the file waiting for import stops, the file goes.
    try:
        import_jobs.stop(user.tenant_id)
    except import_jobs.ImportRunning as error:
        raise HTTPException(status.HTTP_409_CONFLICT, "import_running") from error
    upload = import_uploads.current(user.tenant_id)
    if upload is not None:
        import_uploads.drop(upload)

    keys = await erase_workspace(session, user.tenant_id)
    record(session, AuditAction.WORKSPACE_ERASED, actor=user, details={"files": len(keys)})
    await session.commit()
    for key in keys:
        try:
            await storage.delete_object(key)
        except (storage.StorageNotConfigured, storage.StorageFailed):
            # The rows are gone and the vault key with them; what is left is ciphertext
            # nobody refers to. Ids only in the log: the key holds no client data.
            logger.warning("Object %s of an erased workspace was not removed", key)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
