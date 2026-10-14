import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db.session import get_session

log = logging.getLogger("mockmate.health")
router = APIRouter(tags=["health"])


@router.get("/health")
async def health() -> dict:
    s = get_settings()
    return {"status": "ok", "env": s.app_env, "provider_mode": s.provider_mode}


@router.get("/health/ready")
async def ready(session: AsyncSession = Depends(get_session)) -> dict:
    try:
        await session.execute(text("SELECT 1"))
    except Exception:
        log.exception("readiness check failed")
        raise HTTPException(status_code=503, detail="database unavailable")
    return {"status": "ready", "database": "ok"}