"""Hard per-user daily token cap (docs/PLAN.md §4, risk #7).

Every model call must ``reserve`` its worst-case token count *before* calling
the API, then ``settle`` with the actual usage afterwards. The reservation is a
single conditional UPSERT, so the check and the increment are one atomic step in
Postgres: concurrent calls from a runaway loop cannot jointly exceed the cap.

Each operation commits in its own short user-scoped transaction, independent
of the surrounding request: a reservation must survive the request failing
after the model was already called.

The day boundary is the user's local midnight (``users.timezone``).
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, date, datetime
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from swasthya_api.db.session import user_session


class TokenCapExceeded(Exception):
    def __init__(self, cap: int, used: int, requested: int) -> None:
        super().__init__(f"Daily token cap reached: {used}/{cap} used, {requested} requested")
        self.cap = cap
        self.used = used
        self.requested = requested


@dataclass(frozen=True)
class Usage:
    usage_date: date
    tokens_used: int
    cap: int

    @property
    def remaining(self) -> int:
        return max(self.cap - self.tokens_used, 0)


_RESERVE = text(
    """
    INSERT INTO token_usage (user_id, usage_date, tokens_used, call_count)
    VALUES (:user_id, :usage_date, :tokens, 1)
    ON CONFLICT (user_id, usage_date) DO UPDATE
        SET tokens_used = token_usage.tokens_used + EXCLUDED.tokens_used,
            call_count  = token_usage.call_count + 1,
            updated_at  = now()
        WHERE token_usage.tokens_used + EXCLUDED.tokens_used <= :cap
    RETURNING tokens_used
    """
)

_ADJUST = text(
    """
    UPDATE token_usage
       SET tokens_used = GREATEST(tokens_used + :delta, 0), updated_at = now()
     WHERE user_id = :user_id AND usage_date = :usage_date
    """
)

_USED = text(
    "SELECT tokens_used FROM token_usage WHERE user_id = :user_id AND usage_date = :usage_date"
)

_USER = text("SELECT timezone, daily_token_cap FROM users WHERE id = :user_id")


class TokenBudget:
    """Every statement runs in a user-scoped transaction, so RLS applies to it."""

    def __init__(self, engine: AsyncEngine, user_id: UUID, default_cap: int) -> None:
        self._engine = engine
        self._user_id = user_id
        self._default_cap = default_cap

    async def _context(self, session: AsyncSession, now: datetime | None) -> tuple[date, int]:
        row = (await session.execute(_USER, {"user_id": self._user_id})).one_or_none()
        if row is None:
            # No visible user row (unprovisioned, or RLS hid it): no budget.
            raise TokenCapExceeded(cap=0, used=0, requested=0)
        moment = now or datetime.now(UTC)
        local_day = moment.astimezone(ZoneInfo(row.timezone)).date()
        return local_day, row.daily_token_cap or self._default_cap

    async def usage(self, now: datetime | None = None) -> Usage:
        async with user_session(self._engine, self._user_id) as session:
            day, cap = await self._context(session, now)
            used = await session.scalar(_USED, {"user_id": self._user_id, "usage_date": day})
        return Usage(usage_date=day, tokens_used=used or 0, cap=cap)

    async def reserve(self, tokens: int, now: datetime | None = None) -> date:
        """Atomically claim ``tokens`` from today's budget, or raise."""
        if tokens <= 0:
            raise ValueError("reservation must be positive")
        async with user_session(self._engine, self._user_id) as session:
            day, cap = await self._context(session, now)
            if tokens > cap:
                raise TokenCapExceeded(cap=cap, used=0, requested=tokens)
            claimed = await session.scalar(
                _RESERVE,
                {"user_id": self._user_id, "usage_date": day, "tokens": tokens, "cap": cap},
            )
            if claimed is None:
                used = await session.scalar(_USED, {"user_id": self._user_id, "usage_date": day})
                raise TokenCapExceeded(cap=cap, used=used or 0, requested=tokens)
        return day

    async def settle(self, usage_date: date, reserved: int, actual: int) -> None:
        """Replace a reservation with the actual count (may push past the cap once)."""
        if actual < 0:
            raise ValueError("actual usage cannot be negative")
        async with user_session(self._engine, self._user_id) as session:
            await session.execute(
                _ADJUST,
                {"user_id": self._user_id, "usage_date": usage_date, "delta": actual - reserved},
            )

    @asynccontextmanager
    async def metered(self, max_tokens: int, now: datetime | None = None) -> AsyncIterator["Meter"]:
        """Reserve ``max_tokens``; the caller records actual usage on the meter.

        If the body raises before recording, the full reservation stands — a
        failed call is not a free call, otherwise a crash loop escapes the cap.
        """
        day = await self.reserve(max_tokens, now)
        meter = Meter(reserved=max_tokens)
        yield meter
        if meter.actual is not None:
            await self.settle(day, max_tokens, meter.actual)


@dataclass
class Meter:
    reserved: int
    actual: int | None = None

    def record(self, input_tokens: int, output_tokens: int) -> None:
        self.actual = input_tokens + output_tokens
