from fastapi import APIRouter
from sqlalchemy import text

from app.core.deps import DBDep

router = APIRouter(tags=["health"])


@router.get("/healthz")
async def healthz() -> dict[str, str]:
    """Liveness probe — does not touch dependencies."""
    return {"status": "ok"}


@router.get("/readyz")
async def readyz(db: DBDep) -> dict[str, str]:
    """Readiness probe — verifies DB connectivity."""
    await db.execute(text("SELECT 1"))
    return {"status": "ready"}
