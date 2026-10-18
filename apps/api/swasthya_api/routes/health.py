from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from swasthya_api import __version__
from swasthya_api.deps import get_engine

router = APIRouter(tags=["health"])


class Health(BaseModel):
    status: str
    version: str


@router.get("/health")
async def health() -> Health:
    """Liveness: the process is up. Does not touch the database."""
    return Health(status="ok", version=__version__)


@router.get("/health/ready")
async def ready(engine: Annotated[AsyncEngine, Depends(get_engine)]) -> Health:
    """Readiness: the database answers."""
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "database unavailable") from exc
    return Health(status="ready", version=__version__)
