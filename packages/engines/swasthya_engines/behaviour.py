"""Behaviour engine: six consistency tracks, grace days, rewards, if-then plans.

Deterministic (docs/PLAN.md §5). Design rules it enforces:

* Six independent tracks — a missed day on one never touches another.
* Two grace days per track per month, applied silently, revealed next morning.
* Identity framing ("someone who walks after meals"), never a flame count.
* Rewards are the person's own, never food, never pre-announced, paid
  retroactively at run milestones; outcome rewards only in Sustain.
"""

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date
from enum import StrEnum
from functools import cache
from typing import Any

import yaml
from swasthya_shared.data import data_dir, pillar_keys


@cache
def rules() -> dict[str, Any]:
    return yaml.safe_load((data_dir() / "behaviour/rules.yaml").read_text(encoding="utf-8"))


@cache
def _food_terms() -> tuple[str, ...]:
    raw = yaml.safe_load(
        (data_dir() / "behaviour/reward_food_terms.yaml").read_text(encoding="utf-8")
    )
    return tuple(t.lower() for t in raw["terms"])


class DayResult(StrEnum):
    MET = "met"
    MISSED = "missed"
    GRACE = "grace"  # missed, covered by a grace day
    EXEMPT = "exempt"  # e.g. a fasting day for the eating window
    OBSERVATION = "observation"  # program days 1-3: nothing scored
    NO_DATA = "no_data"


@dataclass(frozen=True)
class TrackState:
    pillar: str
    current_run_days: int = 0
    longest_run_days: int = 0
    last_counted_on: date | None = None


@dataclass(frozen=True)
class TrackUpdate:
    state: TrackState
    result: DayResult
    grace_applied: bool
    milestone: int | None  # run length just reached, if it's a reward milestone


def apply_day(
    state: TrackState, day: date, result: DayResult, *, grace_used_this_month: int
) -> TrackUpdate:
    """Advance one track by one day. Idempotent per day."""
    if state.last_counted_on is not None and day <= state.last_counted_on:
        return TrackUpdate(state, result, False, None)
    run = state.current_run_days
    grace = False
    if result is DayResult.MET:
        run += 1
    elif result is DayResult.MISSED:
        if grace_used_this_month < rules()["grace_days_per_track_per_month"]:
            grace, result = True, DayResult.GRACE  # run survives, silently
        else:
            run = 0
    # EXEMPT, OBSERVATION, NO_DATA: the run neither grows nor breaks.
    new = replace(
        state,
        current_run_days=run,
        longest_run_days=max(state.longest_run_days, run),
        last_counted_on=day,
    )
    milestone = (
        run if result is DayResult.MET and run in rules()["reward_milestones_days"] else None
    )
    return TrackUpdate(new, result, grace, milestone)


def identity(pillar: str) -> str:
    return rules()["identity"][pillar]


# --- Rewards ------------------------------------------------------------------


def is_food_reward(text: str) -> bool:
    low = f" {text.lower()} "
    return any(re.search(rf"\b{re.escape(term)}\b", low) for term in _food_terms())


@dataclass(frozen=True)
class Reward:
    id: Any
    text: str
    kind: str  # process | outcome


def pick_reward(available: Sequence[Reward], *, phase: str) -> Reward | None:
    """The first unpaid reward of the kind this phase pays."""
    outcome_ok = phase == rules()["outcome_rewards_from_phase"]
    for r in available:
        if r.kind == "process" or (r.kind == "outcome" and outcome_ok):
            return r
    return None


def recognition(pillar: str, run: int, reward_text: str) -> str:
    """Retroactive, never announced in advance."""
    return (
        f"{run} days in a row now — you're {identity(pillar)}. "
        f"You once told me you'd enjoy: {reward_text}. This one's earned."
    )


# --- If-then plans --------------------------------------------------------------


def should_retire(execution_count: int, success_count: int) -> bool:
    r = rules()
    return (
        execution_count >= r["if_then_retire_after_executions"]
        and success_count / execution_count >= r["if_then_retire_success_rate"]
    )


# --- Scoring a day --------------------------------------------------------------


@dataclass(frozen=True)
class DayInputs:
    """What is known about one local day."""

    reports: Mapping[str, bool]  # pillar → the person's own check-in answer
    window_kept: bool | None  # from meal timestamps, if any meals were logged
    meals_logged: int
    meals_with_protein: int
    is_fasting_day: bool
    observation: bool


def score_day(inputs: DayInputs) -> dict[str, DayResult]:
    out: dict[str, DayResult] = {}
    for pillar in sorted(pillar_keys()):
        if inputs.observation:
            out[pillar] = DayResult.OBSERVATION
            continue
        if inputs.is_fasting_day and pillar in {"eating_window", "nutrition"}:
            out[pillar] = DayResult.EXEMPT
            continue
        reported = inputs.reports.get(pillar)
        if pillar == "eating_window" and inputs.window_kept is not None:
            # Logged meal times are better evidence than memory.
            out[pillar] = DayResult.MET if inputs.window_kept else DayResult.MISSED
        elif pillar == "nutrition" and reported is None and inputs.meals_logged:
            met = inputs.meals_with_protein == inputs.meals_logged
            out[pillar] = DayResult.MET if met else DayResult.MISSED
        elif pillar == "mind":
            # The mind pillar is the honest check-in itself.
            out[pillar] = DayResult.MET if inputs.reports else DayResult.MISSED
        elif reported is None:
            out[pillar] = DayResult.NO_DATA
        else:
            out[pillar] = DayResult.MET if reported else DayResult.MISSED
    return out
