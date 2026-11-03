import asyncio
import logging

from fastapi import APIRouter, Request, Response, status

from app import __version__
from app.database.session import ping

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/health", tags=["health"])

READINESS_TIMEOUT_SECONDS = 5


@router.get("")
async def liveness() -> dict[str, str]:
    """The process is up and serving requests."""
    return {"status": "ok", "version": __version__}


@router.get("/ready")
async def readiness(request: Request, response: Response) -> dict[str, object]:
    """The process can reach its dependencies."""
    try:
        async with asyncio.timeout(READINESS_TIMEOUT_SECONDS):
            await ping(request.app.state.db_engine)
        database = "ok"
    except Exception as exc:
        logger.warning("Database readiness check failed", extra={"error": type(exc).__name__})
        database = "unavailable"

    ready = database == "ok"
    if not ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return {"status": "ok" if ready else "unavailable", "checks": {"database": database}}
