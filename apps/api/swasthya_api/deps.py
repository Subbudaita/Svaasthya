"""FastAPI dependencies wiring auth → user-scoped DB session."""

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from swasthya_api.auth import CurrentPrincipal
from swasthya_api.budget import TokenBudget
from swasthya_api.db.session import user_session
from swasthya_api.services import Services
from swasthya_api.settings import Settings, get_settings


def get_services(request: Request) -> Services:
    return request.app.state.services


CurrentServices = Annotated[Services, Depends(get_services)]


def get_engine(request: Request) -> AsyncEngine:
    return request.app.state.services.engine


async def get_user_db(
    principal: CurrentPrincipal,
    engine: Annotated[AsyncEngine, Depends(get_engine)],
) -> AsyncIterator[AsyncSession]:
    async with user_session(engine, principal.user_id) as session:
        yield session


UserDb = Annotated[AsyncSession, Depends(get_user_db)]


def get_token_budget(
    principal: CurrentPrincipal,
    engine: Annotated[AsyncEngine, Depends(get_engine)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> TokenBudget:
    return TokenBudget(engine, principal.user_id, settings.daily_token_cap)


Budget = Annotated[TokenBudget, Depends(get_token_budget)]
