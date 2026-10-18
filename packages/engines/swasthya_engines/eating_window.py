"""Eating window: derive it from the person's day, then move it only through gates.

Rules (docs/PLAN.md §4, values in ``data/eating_window.yaml``):

* Anchor the close first: last bite at least 3 h before usual sleep.
* Respect the binding constraint (family dinner) and say so when it conflicts.
* Start from the observed baseline, never from a target.
* Narrow only after a hold period, and only if adherence, energy and sleep
  gates all pass; one end at a time. A failed gate holds or widens back; two
  failures in a row mean review, not more pressure.
* Never below the phase floor (12 h Reset, 10 h after).
"""

import statistics
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, time
from enum import StrEnum
from functools import cache
from typing import Any

import yaml
from swasthya_shared.data import data_dir

from swasthya_engines.clock import after, fmt, fmt_duration, minutes, to_time


@cache
def rules() -> dict[str, Any]:
    return yaml.safe_load((data_dir() / "eating_window.yaml").read_text(encoding="utf-8"))


@dataclass(frozen=True)
class Day:
    wake: time
    sleep: time
    first_bite: time
    last_bite: time
    family_dinner_end: time | None = None


@dataclass(frozen=True)
class Window:
    open: time
    close: time

    @property
    def length(self) -> int:
        o = minutes(self.open)
        return after(o, minutes(self.close)) - o

    def as_dict(self) -> dict[str, str | int]:
        return {"open": fmt(self.open), "close": fmt(self.close), "minutes": self.length}


@dataclass(frozen=True)
class Proposal:
    window: Window
    observed_minutes: int
    why: str
    conflict: str | None


def derive(day: Day, phase: str = "reset") -> Proposal:
    r = rules()
    wake = minutes(day.wake)
    sleep = after(wake, minutes(day.sleep))
    first = after(wake - 180, minutes(day.first_bite))  # tea before waking-ish still counts
    last = after(first, minutes(day.last_bite))
    observed = last - first

    close = sleep - r["close_before_sleep_minutes"]
    conflict = None
    if day.family_dinner_end is not None:
        dinner = after(wake, minutes(day.family_dinner_end))
        if dinner > close:
            gap = sleep - dinner
            conflict = (
                f"Family dinner finishes around {fmt(day.family_dinner_end)}, only "
                f"{fmt_duration(max(gap, 0))} before you sleep — less than the 3 h we'd like. "
                "Dinner with family stays; the window is built around it, and sleep timing is "
                "the thing to work on later."
            )
            close = dinner

    floor = r["floor_minutes"][phase]
    target = (
        max(floor, observed - r["max_initial_reduction_minutes"]) if observed > floor else floor
    )
    open_ = max(close - target, wake)
    window = Window(to_time(open_), to_time(close))

    why = (
        f"You usually sleep around {fmt(day.sleep)}, so the kitchen closes at {fmt(window.close)}"
        + (" — after family dinner." if conflict else ", three hours before bed.")
        + f" Yesterday you ate across {fmt_duration(observed)}; we start at "
        f"{fmt_duration(window.length)}, not 16:8 — moderate and consistent beats extreme."
    )
    return Proposal(window, observed, why, conflict)


class Action(StrEnum):
    HOLD = "hold"
    NARROW = "narrow"
    WIDEN = "widen"
    REVIEW = "review"


@dataclass(frozen=True)
class DayRecord:
    day: date
    kept: bool | None  # None: exempt (fasting day) or not enough data
    energy: int | None = None
    sleep_minutes: int | None = None


@dataclass(frozen=True)
class Decision:
    action: Action
    window: Window
    reason: str


def kept(window: Window, first_bite: time | None, last_bite: time | None) -> bool | None:
    """Was the window kept on a day? None if no first/last bite is known."""
    if first_bite is None or last_bite is None:
        return None
    tol = rules()["tolerance_minutes"]
    o = minutes(window.open)
    first = after(o - 6 * 60, minutes(first_bite))
    last = after(first, minutes(last_bite))
    return first >= o - tol and last <= o + window.length + tol


def _halves(values: Sequence[float]) -> tuple[float, float] | None:
    if len(values) < 4:
        return None
    mid = len(values) // 2
    return statistics.fmean(values[:mid]), statistics.fmean(values[mid:])


def next_step(
    window: Window,
    *,
    since_change: Sequence[DayRecord],
    consecutive_failures: int,
    phase: str,
    sleep_time: time,
) -> Decision:
    r = rules()
    p = r["progression"]
    days = len(since_change)
    if days < p["hold_days_min"]:
        return Decision(
            Action.HOLD, window, f"Holding: day {days} of at least {p['hold_days_min']}."
        )

    scored = [d for d in since_change if d.kept is not None]
    adherence = sum(d.kept for d in scored) / len(scored) if scored else 0.0
    energy = _halves([d.energy for d in scored if d.energy is not None])
    sleep = _halves([d.sleep_minutes for d in scored if d.sleep_minutes is not None])
    failed = []
    if adherence < p["adherence_gate"]:
        failed.append(f"window kept on {adherence:.0%} of days (gate {p['adherence_gate']:.0%})")
    if energy and energy[1] < energy[0] - p["energy_drop_allowed"]:
        failed.append("energy has been falling")
    if sleep and sleep[1] < sleep[0] - p["sleep_drop_allowed_minutes"]:
        failed.append("sleep has been getting shorter")

    if failed:
        if consecutive_failures + 1 >= p["consecutive_failures_for_review"]:
            return Decision(
                Action.REVIEW,
                window,
                "Two checks in a row didn't pass (" + "; ".join(failed) + "). The window isn't "
                "the problem — time to look at what's getting in the way, not to push harder.",
            )
        if adherence < p["adherence_gate"] and days >= p["hold_days_max"]:
            wider = Window(to_time(minutes(window.open) - p["step_minutes_min"]), window.close)
            return Decision(
                Action.WIDEN, wider, "Widening back a little: " + "; ".join(failed) + "."
            )
        return Decision(Action.HOLD, window, "Holding: " + "; ".join(failed) + ".")

    floor = r["floor_minutes"][phase]
    step = p["step_minutes_max"] if adherence >= 0.95 else p["step_minutes_min"]
    if window.length - step < floor:
        step = window.length - floor
    if step <= 0:
        return Decision(Action.HOLD, window, f"At the {fmt_duration(floor)} destination. Holding.")

    # One end at a time: bring the close earlier while it is within 3 h of sleep,
    # otherwise move the open later.
    sleep_m = after(minutes(window.open), minutes(sleep_time))
    close_m = minutes(window.open) + window.length
    if sleep_m - close_m < r["close_before_sleep_minutes"]:
        narrower = Window(window.open, to_time(close_m - step))
        end = f"close moves to {fmt(narrower.close)}"
    else:
        narrower = Window(to_time(minutes(window.open) + step), window.close)
        end = f"open moves to {fmt(narrower.open)}"
    return Decision(
        Action.NARROW,
        narrower,
        f"All three checks passed (kept {adherence:.0%}, energy steady, sleep steady): {end}.",
    )
