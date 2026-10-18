"""Database engine and user-scoped sessions.

Tenant isolation is enforced by Postgres row-level security, not by this
module. All this module does is tell Postgres who is asking: every request runs
inside one transaction that begins with ``set_config('app.user_id', ..., true)``.
The setting is transaction-local, so a pooled connection never carries one
user's identity into another user's request, and a query issued outside such a
transaction sees no rows at all (RLS fails closed).
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine

USER_ID_SETTING = "app.user_id"


class RlsNotEnforcedError(RuntimeError):
    """The runtime DB role could bypass row-level security."""


def create_engine(database_url: str) -> AsyncEngine:
    return create_async_engine(database_url, pool_pre_ping=True)


async def assert_rls_enforced(engine: AsyncEngine) -> None:
    """Refuse to run as a role that RLS does not apply to."""
    async with engine.connect() as conn:
        row = (
            await conn.execute(
                text("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user")
            )
        ).one()
    if row.rolsuper or row.rolbypassrls:
        raise RlsNotEnforcedError(
            "DATABASE_URL connects as a superuser or BYPASSRLS role; "
            "use the swasthya_api login role (see README)."
        )


@asynccontextmanager
async def user_session(engine: AsyncEngine, user_id: UUID) -> AsyncIterator[AsyncSession]:
    """One transaction, scoped to ``user_id`` for RLS. Commits on success."""
    async with AsyncSession(engine, expire_on_commit=False) as session, session.begin():
        await session.execute(
            text("SELECT set_config(:key, :value, true)"),
            {"key": USER_ID_SETTING, "value": str(user_id)},
        )
        yield session
