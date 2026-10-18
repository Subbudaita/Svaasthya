from datetime import date, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from swasthya_api.auth import CurrentPrincipal
from swasthya_api.db.models import User
from swasthya_api.deps import Budget, UserDb
from swasthya_api.settings import Settings, get_settings

router = APIRouter(prefix="/v1/me", tags=["me"])


class Me(BaseModel):
    id: UUID
    email: str | None
    display_name: str | None
    timezone: str
    onboarded: bool
    created_at: datetime


class TokenUsageOut(BaseModel):
    usage_date: date
    tokens_used: int
    cap: int
    remaining: int


@router.get("")
async def read_me(
    principal: CurrentPrincipal,
    db: UserDb,
    settings: Annotated[Settings, Depends(get_settings)],
) -> Me:
    """Return the caller, provisioning their row on first sight.

    Sign-up itself happens in Supabase Auth (invite-only there); this only
    mirrors the authenticated identity into the tenant schema.
    """
    await db.execute(
        insert(User)
        .values(id=principal.user_id, email=principal.email, timezone=settings.default_timezone)
        .on_conflict_do_nothing(index_elements=[User.id])
    )
    user = (await db.execute(select(User).where(User.id == principal.user_id))).scalar_one()
    return Me(
        id=user.id,
        email=user.email,
        display_name=user.display_name,
        timezone=user.timezone,
        onboarded=user.onboarded_at is not None,
        created_at=user.created_at,
    )


@router.get("/usage")
async def read_usage(budget: Budget) -> TokenUsageOut:
    usage = await budget.usage()
    return TokenUsageOut(
        usage_date=usage.usage_date,
        tokens_used=usage.tokens_used,
        cap=usage.cap,
        remaining=usage.remaining,
    )
