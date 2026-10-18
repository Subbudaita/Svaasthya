"""The 07:00 brief and the 21:30 check-in, composed deterministically.

Phase 2 renders these from templates and data; Phase 3 lets the agent
personalise wording within the same facts. Nothing here shows a calorie
number, and during Reset nothing shows a gram target either.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, time
from functools import cache
from typing import Any

import yaml
from swasthya_shared.data import data_dir, pillars

from swasthya_engines.clock import fmt


@cache
def schedule() -> dict[str, Any]:
    return yaml.safe_load((data_dir() / "rituals/schedule.yaml").read_text(encoding="utf-8"))


@cache
def _breakfasts() -> list[dict[str, Any]]:
    return yaml.safe_load(
        (data_dir() / "rituals/breakfast_options.yaml").read_text(encoding="utf-8")
    )["options"]


@cache
def _questions() -> list[dict[str, Any]]:
    return yaml.safe_load(
        (data_dir() / "rituals/causal_questions.yaml").read_text(encoding="utf-8")
    )["questions"]


def default_time(ritual: str) -> time:
    return time.fromisoformat(schedule()[ritual])


@dataclass(frozen=True)
class OwnBreakfast:
    label: str
    has_protein: bool
    times_eaten: int


def choose_breakfast(own: Sequence[OwnBreakfast], diet_pattern: str, day: date) -> tuple[str, str]:
    """Pre-decide breakfast (P2): the person's own protein breakfast first.

    Returns (label, source) where source is "yours" or "suggested".
    """
    mine = sorted((b for b in own if b.has_protein), key=lambda b: -b.times_eaten)
    if mine:
        return mine[0].label, "yours"
    options = [o for o in _breakfasts() if diet_pattern in o["diets"]] or _breakfasts()
    pick = options[day.toordinal() % len(options)]  # rotates, deterministic per day
    return pick["label"], "suggested"


@dataclass(frozen=True)
class BriefFacts:
    day: date
    phase: str
    program_day: int
    observation_days: int
    window: tuple[time, time] | None
    is_fasting_day: bool
    breakfast: tuple[str, str]
    yesterday_window: str | None  # "kept" / "missed" / None
    yesterday_last_bite: time | None
    grace_reveals: Sequence[str]  # identity lines for tracks a grace day covered
    recognitions: Sequence[str]
    clause_lines: Sequence[str]


def compose_brief(f: BriefFacts) -> str:
    lines = [f"Good morning — {f.day:%A, %d %b}."]
    if f.program_day <= f.observation_days:
        lines.append(
            f"Day {f.program_day} of {f.observation_days} where I only watch. Eat and log "
            "the way you normally do; nothing is scored yet."
        )
    for text in f.recognitions:
        lines += ["", text]
    if f.grace_reveals:
        lines += [
            "",
            "Yesterday slipped on " + _join(f.grace_reveals) + ", and a grace day "
            "covered it — nothing broke. You're still that person; today's a normal day.",
        ]
    if f.yesterday_window == "missed" and f.yesterday_last_bite:
        lines += ["", f"Last bite yesterday was {fmt(f.yesterday_last_bite)}."]
    lines.append("")
    if f.is_fasting_day:
        lines.append("Today is one of your fasting days — the window and plates aren't scored.")
    elif f.window:
        lines.append(f"Kitchen opens {fmt(f.window[0])}, closes {fmt(f.window[1])}.")
    label, source = f.breakfast
    lines.append(
        f"Breakfast, decided: {label}" + (" — your own go-to." if source == "yours" else ".")
    )
    if f.clause_lines:
        lines += ["", "Today:"] + [f"• {c}" for c in f.clause_lines]
    return "\n".join(lines)


def _join(items: Sequence[str]) -> str:
    items = list(items)
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


def choose_question(signals: Mapping[str, bool]) -> tuple[str, str]:
    """Exactly one causal question: the first whose trigger fired today."""
    for q in _questions():
        if q["when"] == "always" or signals.get(q["when"]):
            return q["id"], q["text"]
    raise ValueError("causal_questions.yaml needs an 'always' question")


def checkin_prompt(question: str) -> str:
    return (
        "Evening check-in. Tap each pillar for today — honest beats good.\n\n"
        f"Then one question: {question}\n(Just reply with a sentence or two.)"
    )


def pillar_labels() -> list[tuple[str, str]]:
    return [(p["key"], p["label"]) for p in pillars()]
