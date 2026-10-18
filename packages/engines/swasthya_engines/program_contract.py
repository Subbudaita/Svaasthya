"""The Program Contract rule engine (docs/PLAN.md F6, §5).

Deterministic: phase template + the person's derived parameters + safety
screen → a versioned JSON contract. Every clause carries its id and why it
applies; recommendations elsewhere cite these ids. The LLM (Phase 3)
personalises wording only within the bounds written here.
"""

import string
from dataclasses import dataclass, field
from datetime import time
from functools import cache
from typing import Any

import yaml
from swasthya_shared.data import data_dir

from swasthya_engines.clock import fmt
from swasthya_engines.contraindications import Screen
from swasthya_engines.eating_window import Proposal, Window

GENERATOR_VERSION = "contract-engine/1"
PHASES = ("reset", "rebuild", "sustain")

WALKING_LABELS = {
    "under_2km": "under 2 km",
    "2_to_5km": "2–5 km",
    "over_5km": "over 5 km",
}


@cache
def template(phase: str) -> dict[str, Any]:
    if phase not in PHASES:
        raise ValueError(f"unknown phase {phase!r}")
    return yaml.safe_load(
        (data_dir() / f"phase_templates/{phase}.yaml").read_text(encoding="utf-8")
    )


@dataclass(frozen=True)
class ProgramInputs:
    weight_kg: float
    sleep_time: time
    walking: str
    diet_pattern: str
    fasting_days: tuple[str, ...] = ()
    goal: str | None = None
    window: Proposal | None = None  # None only when time-restricted eating is gated off
    window_override: Window | None = None  # a window moved by progression
    extras: dict[str, Any] = field(default_factory=dict)


class _Strict(string.Formatter):
    def get_value(self, key, args, kwargs):  # noqa: ANN001
        if key not in kwargs:
            raise KeyError(f"template placeholder {{{key}}} has no value")
        return kwargs[key]


def _applies(clause: dict[str, Any], screen: Screen) -> bool:
    absent = clause.get("requires_flag_absent")
    present = clause.get("requires_flag_present")
    if absent and screen.has(absent):
        return False
    if present and not screen.has(present):
        return False
    return True


def build(phase: str, inputs: ProgramInputs, screen: Screen) -> dict[str, Any]:
    tpl = template(phase)
    params = tpl.get("params", {})
    trf_allowed = not screen.has("no_time_restricted_eating")
    window = inputs.window_override or (inputs.window.window if inputs.window else None)
    if trf_allowed and window is None:
        raise ValueError("an eating window is required unless time-restricted eating is gated off")

    protein_target = (
        round(inputs.weight_kg * params["protein_g_per_kg"])
        if "protein_g_per_kg" in params and not screen.has("protein_standard_only")
        else None
    )
    no_trf_reason = "; ".join(
        reason
        for key, reason in screen.reasons
        if key
        in {
            "pregnant_or_breastfeeding",
            "type_1_diabetes",
            "insulin_or_sulfonylurea",
            "eating_disorder_history",
            "underweight",
        }
    )
    values = {
        "window_open": fmt(window.open) if window else "",
        "window_close": fmt(window.close) if window else "",
        "window_why": (inputs.window.why if inputs.window else "") or "",
        "no_trf_reason": no_trf_reason,
        "walking_baseline": WALKING_LABELS.get(inputs.walking, inputs.walking),
        "sleep_time": fmt(inputs.sleep_time),
        "protein_target_g": protein_target or "",
    }
    fmt_ = _Strict()
    clauses = [
        {
            "id": c["id"],
            "pillar": c["pillar"],
            "text": fmt_.format(c["text"], **values),
            "why": fmt_.format(c["why"], **values),
        }
        for c in tpl["clauses"]
        if _applies(c, screen)
    ]
    return {
        "phase": phase,
        "template_version": tpl["version"],
        "generated_by": GENERATOR_VERSION,
        "numbers_visible": tpl["numbers_visible"],
        "observation_days": tpl.get("observation_days", 0),
        "goal": inputs.goal,
        "flags": sorted(screen.flags),
        "refer_first": screen.has("refer_first"),
        "safety_reasons": [{"key": k, "reason": r} for k, r in screen.reasons],
        "prohibitions": list(screen.prohibitions),
        "parameters": {
            "eating_window": window.as_dict() if window and trf_allowed else None,
            "eating_window_observed_minutes": inputs.window.observed_minutes
            if inputs.window
            else None,
            "eating_window_conflict": inputs.window.conflict if inputs.window else None,
            "protein_target_g": protein_target,
            "sleep_time": fmt(inputs.sleep_time),
            "walking_baseline": inputs.walking,
            "diet_pattern": inputs.diet_pattern,
            "fasting_days": list(inputs.fasting_days),
        },
        "clauses": clauses,
    }


def clause(contract: dict[str, Any], clause_id: str) -> dict[str, Any] | None:
    return next((c for c in contract["clauses"] if c["id"] == clause_id), None)


def window_of(contract: dict[str, Any]) -> Window | None:
    w = contract["parameters"].get("eating_window")
    if not w:
        return None
    return Window(time.fromisoformat(w["open"]), time.fromisoformat(w["close"]))


def summary_text(contract: dict[str, Any]) -> str:
    """The contract as a person reads it: each clause, and why."""
    lines = [f"Your program — {contract['phase'].title()} phase", ""]
    if contract["refer_first"]:
        lines += [
            "First: please make sure your doctor knows you're starting this. "
            + " ".join(r["reason"] for r in contract["safety_reasons"]),
            "",
        ]
    for c in contract["clauses"]:
        lines.append(f"• {c['text']}\n  Why: {c['why']}")
    conflict = contract["parameters"].get("eating_window_conflict")
    if conflict:
        lines += ["", conflict]
    fasting = contract["parameters"].get("fasting_days")
    if fasting:
        lines += [
            "",
            "Your fasting days (" + ", ".join(fasting) + ") are respected and never "
            "scored against you.",
        ]
    if contract["observation_days"]:
        lines += [
            "",
            f"For the first {contract['observation_days']} days I only watch — nothing "
            "is scored. Log the way you normally eat.",
        ]
    return "\n".join(lines)
