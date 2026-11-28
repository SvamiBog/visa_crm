"""A change of the base currency, for both places that make one (pilot T8): the
specialist's «Настройки» (PATCH /dictionaries/money) and the admin's page of her workspace
(PATCH /admin/specialists/{id}). The rules are in app/core/base_currency.py."""

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.base_currency import RateMissing, Recounted, change_base_currency
from app.core.finance import AmountTooLarge
from app.core.import_jobs import ImportRunning
from app.models import Tenant


async def change_base(session: AsyncSession, tenant: Tenant, new: str) -> Recounted:
    """Count the workspace's amounts in `new`; the caller records the audit event and
    commits. An import writing now, a rate the site lacks or an amount the new base cannot
    keep: an error, nothing changed."""
    try:
        return await change_base_currency(session, tenant, new)
    except ImportRunning as error:
        raise HTTPException(status.HTTP_409_CONFLICT, "import_running") from error
    except RateMissing as error:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "base_rate_unavailable"
        ) from error
    except AmountTooLarge as error:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "base_amount_too_large"
        ) from error
