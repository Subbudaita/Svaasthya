import asyncio
from datetime import UTC, datetime

import pytest
from swasthya_api.budget import TokenBudget, TokenCapExceeded

DEFAULT_CAP = 10_000


def budget(engine, user_id, cap=DEFAULT_CAP) -> TokenBudget:
    return TokenBudget(engine, user_id, cap)


async def test_reserve_within_cap(engine, make_user):
    user = make_user()
    b = budget(engine, user)
    await b.reserve(4_000)
    await b.reserve(6_000)  # exactly at the cap is allowed
    usage = await b.usage()
    assert (usage.tokens_used, usage.remaining) == (10_000, 0)


async def test_reserve_over_cap_is_refused_and_not_recorded(engine, make_user):
    user = make_user()
    b = budget(engine, user)
    await b.reserve(9_000)
    with pytest.raises(TokenCapExceeded) as info:
        await b.reserve(1_001)
    assert (info.value.cap, info.value.used, info.value.requested) == (10_000, 9_000, 1_001)
    assert (await b.usage()).tokens_used == 9_000


async def test_single_request_larger_than_cap(engine, make_user):
    b = budget(engine, make_user())
    with pytest.raises(TokenCapExceeded):
        await b.reserve(DEFAULT_CAP + 1)


async def test_runaway_concurrent_loop_cannot_exceed_cap(engine, make_user):
    """50 parallel calls of 1k against a 10k cap: exactly 10 succeed."""
    user = make_user()
    b = budget(engine, user)

    async def attempt() -> bool:
        try:
            await b.reserve(1_000)
            return True
        except TokenCapExceeded:
            return False

    results = await asyncio.gather(*(attempt() for _ in range(50)))
    assert sum(results) == 10
    assert (await b.usage()).tokens_used == 10_000


async def test_per_user_cap_override(engine, make_user):
    user = make_user(daily_token_cap=500)
    b = budget(engine, user)
    await b.reserve(500)
    with pytest.raises(TokenCapExceeded):
        await b.reserve(1)


async def test_users_have_independent_budgets(engine, make_user):
    alice, bob = make_user(), make_user()
    await budget(engine, alice).reserve(DEFAULT_CAP)
    await budget(engine, bob).reserve(DEFAULT_CAP)  # Alice's spend does not affect Bob


async def test_day_resets_at_users_local_midnight(engine, make_user):
    user = make_user(timezone="Asia/Kolkata")
    b = budget(engine, user)
    # 18:29 UTC = 23:59 IST; 18:31 UTC = 00:01 IST next day.
    before = datetime(2026, 10, 18, 18, 29, tzinfo=UTC)
    after = datetime(2026, 10, 18, 18, 31, tzinfo=UTC)
    await b.reserve(DEFAULT_CAP, now=before)
    with pytest.raises(TokenCapExceeded):
        await b.reserve(1, now=before)
    await b.reserve(DEFAULT_CAP, now=after)
    assert (await b.usage(now=after)).usage_date.isoformat() == "2026-10-19"


async def test_metered_settles_to_actual_usage(engine, make_user):
    b = budget(engine, make_user())
    async with b.metered(max_tokens=4_000) as meter:
        meter.record(input_tokens=1_200, output_tokens=300)
    assert (await b.usage()).tokens_used == 1_500


async def test_failed_call_keeps_full_reservation(engine, make_user):
    """A crash after reserving is not free — otherwise a crash loop escapes the cap."""
    b = budget(engine, make_user())
    with pytest.raises(RuntimeError):
        async with b.metered(max_tokens=4_000):
            raise RuntimeError("model call failed")
    assert (await b.usage()).tokens_used == 4_000


async def test_unknown_user_has_no_budget(engine):
    import uuid

    with pytest.raises(TokenCapExceeded):
        await budget(engine, uuid.uuid4()).reserve(1)


def test_usage_endpoint_and_429(client, make_token, settings):
    import uuid

    user = uuid.uuid4()
    headers = {"Authorization": f"Bearer {make_token(user)}"}
    assert client.get("/v1/me", headers=headers).status_code == 200
    body = client.get("/v1/me/usage", headers=headers).json()
    assert body["tokens_used"] == 0
    assert body["cap"] == settings.daily_token_cap
    assert body["remaining"] == settings.daily_token_cap


def test_cap_exceeded_maps_to_429(settings):
    from fastapi.testclient import TestClient
    from swasthya_api.main import create_app

    app = create_app(settings)

    @app.get("/_test/exhaust")
    async def _exhaust():
        raise TokenCapExceeded(cap=10, used=10, requested=1)

    with TestClient(app) as c:
        r = c.get("/_test/exhaust")
    assert r.status_code == 429
    assert r.json()["detail"] == "daily_token_cap_reached"
