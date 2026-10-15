from fastapi import APIRouter
from sqlalchemy import text

from app.core.config import get_settings
from app.core.db import get_engine

router = APIRouter()


@router.get("/health")
async def health(db: bool = False) -> dict[str, str]:
    """Liveness probe. /api/health?db=1 also checks the database connection."""
    result = {"status": "ok", "env": get_settings().env}
    if db:
        try:
            async with get_engine().connect() as conn:
                await conn.execute(text("SELECT 1"))
            result["db"] = "ok"
        except Exception:  # report the failure instead of crashing the probe
            result["db"] = "error"
    return result
