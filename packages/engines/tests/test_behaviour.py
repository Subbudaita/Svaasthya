from datetime import date

import pytest
from swasthya_engines.behaviour import (
    DayInputs,
    DayResult,
    Reward,
    TrackState,
    apply_day,
    is_food_reward,
    pick_reward,
    recognition,
    score_day,
    should_retire,
)

D = date(2026, 10, 1)


def run_days(results, grace_used=0):
    state, out = TrackState("movement"), []
    for i, r in enumerate(results):
        upd = apply_day(state, date(2026, 10, i + 1), r, grace_used_this_month=grace_used)
        if upd.grace_applied:
            grace_used += 1
        state = upd.state
        out.append(upd)
    return state, out


def test_met_days_build_a_run():
    state, _ = run_days([DayResult.MET] * 5)
    assert state.current_run_days == 5 and state.longest_run_days == 5


def test_two_grace_days_then_the_run_breaks():
    state, out = run_days(
        [
            DayResult.MET,
            DayResult.MISSED,
            DayResult.MET,
            DayResult.MISSED,
            DayResult.MET,
            DayResult.MISSED,
        ]
    )
    assert [u.grace_applied for u in out] == [False, True, False, True, False, False]
    assert state.current_run_days == 0 and state.longest_run_days == 3


def test_never_more_than_two_grace_days_a_month():
    _, out = run_days([DayResult.MISSED] * 6)
    assert sum(u.grace_applied for u in out) == 2


def test_exempt_and_observation_neither_grow_nor_break():
    state, _ = run_days(
        [DayResult.MET, DayResult.EXEMPT, DayResult.OBSERVATION, DayResult.NO_DATA, DayResult.MET]
    )
    assert state.current_run_days == 2


def test_apply_day_is_idempotent():
    s1 = apply_day(TrackState("sleep"), D, DayResult.MET, grace_used_this_month=0).state
    s2 = apply_day(s1, D, DayResult.MET, grace_used_this_month=0).state
    assert s2 == s1


def test_milestone_and_retroactive_recognition():
    _, out = run_days([DayResult.MET] * 14)
    assert out[-1].milestone == 14 and all(u.milestone is None for u in out[:-1])
    text = recognition("movement", 14, "new running shoes")
    assert "someone who walks after meals" in text and "🔥" not in text


@pytest.mark.parametrize("text", ["a pizza night", "Chocolate cake", "cheat meal", "biryani"])
def test_food_rewards_rejected(text):
    assert is_food_reward(text)


@pytest.mark.parametrize("text", ["new running shoes", "a massage", "day trip to Nandi Hills"])
def test_non_food_rewards_allowed(text):
    assert not is_food_reward(text)


def test_outcome_rewards_only_in_sustain():
    rewards = [Reward(1, "a weekend away", "outcome"), Reward(2, "a book", "process")]
    assert pick_reward(rewards, phase="reset").id == 2
    assert pick_reward(rewards[:1], phase="rebuild") is None
    assert pick_reward(rewards[:1], phase="sustain").id == 1


def test_if_then_retires_once_automatic():
    assert should_retire(14, 13)
    assert not should_retire(10, 10)
    assert not should_retire(20, 15)


def test_score_day():
    s = score_day(
        DayInputs(
            reports={"movement": True, "sleep": False},
            window_kept=True,
            meals_logged=2,
            meals_with_protein=1,
            is_fasting_day=False,
            observation=False,
        )
    )
    assert s["eating_window"] is DayResult.MET
    assert s["nutrition"] is DayResult.MISSED  # derived from plates when not reported
    assert s["movement"] is DayResult.MET and s["sleep"] is DayResult.MISSED
    assert s["hydration"] is DayResult.NO_DATA
    assert s["mind"] is DayResult.MET
    fasting = score_day(
        DayInputs(
            reports={},
            window_kept=False,
            meals_logged=0,
            meals_with_protein=0,
            is_fasting_day=True,
            observation=False,
        )
    )
    assert fasting["eating_window"] is DayResult.EXEMPT and fasting["mind"] is DayResult.MISSED
    watching = score_day(
        DayInputs(
            reports={},
            window_kept=False,
            meals_logged=0,
            meals_with_protein=0,
            is_fasting_day=False,
            observation=True,
        )
    )
    assert set(watching.values()) == {DayResult.OBSERVATION}
