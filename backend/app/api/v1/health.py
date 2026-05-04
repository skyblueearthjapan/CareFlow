"""Health-check endpoints.

- /healthz : liveness — returns 200 immediately, no dependencies.
- /readyz  : readiness — verifies DB connectivity, returns 503 on failure.
"""

from __future__ import annotations

from fastapi import APIRouter, Response, status
from sqlalchemy import text

from app.core.deps import DbDep

router = APIRouter(tags=["health"])


@router.get("/healthz", summary="Liveness probe")
async def healthz() -> dict[str, str]:
    """Always return 200 if the process is running."""
    return {"status": "ok"}


@router.get("/readyz", summary="Readiness probe (DB ping)")
async def readyz(db: DbDep, response: Response) -> dict[str, str]:
    """Return 200 + status=ready when DB is reachable, else 503."""
    try:
        await db.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001 - DB drivers raise many specific types
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {"status": "unavailable", "reason": exc.__class__.__name__}
    return {"status": "ready"}
