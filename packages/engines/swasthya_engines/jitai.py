"""JITAI decision rules — deterministic, rate-limited, logged (docs/PLAN.md F5).

Components, in JITAI terms:
* decision points  — each rule's local time (``at``), checked every tick;
* tailoring variables — ``Tailoring``, computed from the person's own data;
* decision rules   — ``data/jitai.yaml``, all conditions must hold;
* intervention options — the rule's message, filled from tailoring variables;
* proximal outcome — named per rule, scored the next morning.
"""

import statistics
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from functools import cache
from typing import Any

import yaml
from swasthya_shared.data import data_dir

from swasthya_engines.clock import fmt, minutes, to_time


@cache
def rules() -> dict[str, Any]:
    return yaml.safe_load((data_dir() / "jitai.yaml").read_text(encoding="utf-8"))


@dataclass(frozen=True)
class Tailoring:
    """Everything a rule may look at, for one person at one moment."""

    local_now: datetime
    window_open: time | None
    window_close: time | None
    energy_dip_time: time | None
    breakfast_plan: str | None
    short_sleep_last_night: bool
    meals_today: int
    dinner_logged: bool
    late_last_bites_7d: Sequence[time] = field(default_factory=tuple)
    breakfast_missing_days_7d: int = 0

    @property
    def lapse_clock(self) -> time | None:
        """The person's typical late-eating time: median of recent late last bites."""
        if not self.late_last_bites_7d:
            return None
        # Late bites can cross midnight; measure them from noon.
        offsets = [(minutes(t) - 720) % 1440 for t in self.late_last_bites_7d]
        return to_time(int(statistics.median(offsets)) + 720)


@dataclass(frozen=True)
class Gate:
    in_observation: bool
    is_fasting_day: bool
    sent_last_7_days: int
    sent_today: int
    ritual_times: Sequence[time]


def _anchor(name: str, t: Tailoring) -> time | None:
    return {
        "energy_dip_time": t.energy_dip_time,
        "window_open": t.window_open,
        "window_close": t.window_close,
    }[name]


def _condition(name: str, t: Tailoring) -> bool:
    th = rules()["thresholds"]
    match name:
        case "has_energy_dip_time":
            return t.energy_dip_time is not None
        case "short_sleep_last_night":
            return t.short_sleep_last_night
        case "no_meal_logged_today":
            return t.meals_today == 0
        case "breakfast_slipped_recently":
            return t.breakfast_missing_days_7d >= th["breakfast_slipped_recently"]
        case "no_dinner_logged_yet":
            return not t.dinner_logged
        case "late_lapses_recently":
            return len(t.late_last_bites_7d) >= th["late_lapses_recently"]
    raise ValueError(f"unknown JITAI condition {name!r}")


@dataclass(frozen=True)
class Nudge:
    rule_id: str
    failure_mode: str
    outcome: str
    text: str
    tailoring: dict[str, Any]


def decide(t: Tailoring, gate: Gate) -> tuple[str, Nudge | None]:
    """Return (decision, nudge). Decision is 'send', 'suppressed:<why>' or 'none'."""
    r = rules()
    now_m = minutes(t.local_now.time())
    for rule in r["rules"]:
        anchor = _anchor(rule["at"]["anchor"], t)
        if anchor is None:
            continue
        at = (minutes(anchor) + rule["at"]["offset_minutes"]) % 1440
        if not 0 <= (now_m - at) % 1440 < r["window_minutes"]:
            continue
        if not all(_condition(c, t) for c in rule["when"]):
            continue
        nudge = Nudge(
            rule_id=rule["id"],
            failure_mode=rule["failure_mode"],
            outcome=rule["outcome"],
            text=" ".join(rule["message"].format(**_values(t)).split()),
            tailoring=_snapshot(t),
        )
        if gate.in_observation:
            return "suppressed:observation", nudge
        if gate.is_fasting_day and rule["id"] != "energy_dip":
            return "suppressed:fasting_day", nudge
        if gate.sent_last_7_days >= r["weekly_cap"]:
            return "suppressed:weekly_cap", nudge
        if gate.sent_today >= r["daily_cap"]:
            return "suppressed:daily_cap", nudge
        quiet = r["quiet_around_rituals_minutes"]
        if any(abs((now_m - minutes(rt) + 720) % 1440 - 720) < quiet for rt in gate.ritual_times):
            return "suppressed:near_ritual", nudge
        return "send", nudge
    return "none", None


def _values(t: Tailoring) -> dict[str, str]:
    return {
        "energy_dip": fmt(t.energy_dip_time) if t.energy_dip_time else "",
        "breakfast": t.breakfast_plan or "a breakfast with protein",
        "window_close": fmt(t.window_close) if t.window_close else "",
        "late_lapses": str(len(t.late_last_bites_7d)),
        "lapse_clock": fmt(t.lapse_clock) if t.lapse_clock else "late",
    }


def _snapshot(t: Tailoring) -> dict[str, Any]:
    return {
        "local_now": t.local_now.isoformat(),
        "short_sleep_last_night": t.short_sleep_last_night,
        "meals_today": t.meals_today,
        "dinner_logged": t.dinner_logged,
        "late_lapses_7d": len(t.late_last_bites_7d),
        "lapse_clock": fmt(t.lapse_clock) if t.lapse_clock else None,
        "breakfast_missing_days_7d": t.breakfast_missing_days_7d,
    }


def outcome_met(
    outcome: str,
    *,
    sent_at: datetime,
    meal_times: Sequence[datetime],
    protein_meal_times: Sequence[datetime],
    window_kept: bool | None,
) -> bool | None:
    """Proximal outcome of a nudge, judged from what happened after it."""
    match outcome:
        case "protein_logged_within_120_min":
            return any(sent_at <= m <= sent_at + timedelta(minutes=120) for m in protein_meal_times)
        case "meal_logged_within_180_min":
            return any(sent_at <= m <= sent_at + timedelta(minutes=180) for m in meal_times)
        case "window_kept_today":
            return window_kept
    raise ValueError(f"unknown outcome {outcome!r}")
