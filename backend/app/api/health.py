from fastapi import APIRouter
from sqlalchemy import text

from app.core.db import get_engine

router = APIRouter()


@router.get("/health")
async def health(db: bool = False) -> dict[str, str]:
    """Liveness probe. /api/health?db=1 also checks the database connection. It does not
    name the environment (audit h14.5): anyone may ask it."""
    result = {"status": "ok"}
    if db:
        try:
            async with get_engine().connect() as conn:
                await conn.execute(text("SELECT 1"))
            result["db"] = "ok"
        except Exception:  # report the failure instead of crashing the probe
            result["db"] = "error"
    return result
