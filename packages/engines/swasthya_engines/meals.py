"""Meal slots and repeat-meal fingerprints. Deterministic."""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import time
from functools import cache
from typing import Any

import yaml
from swasthya_shared.data import data_dir


@dataclass(frozen=True)
class Slot:
    name: str
    start: time
    end: time

    def contains(self, t: time) -> bool:
        if self.start <= self.end:
            return self.start <= t < self.end
        return t >= self.start or t < self.end  # wraps midnight


@cache
def slots() -> tuple[Slot, ...]:
    raw = yaml.safe_load((data_dir() / "meal_slots.yaml").read_text(encoding="utf-8"))
    return tuple(
        Slot(s["name"], time.fromisoformat(s["start"]), time.fromisoformat(s["end"]))
        for s in raw["slots"]
    )


def slot_for(t: time) -> str:
    for slot in slots():
        if slot.contains(t):
            return slot.name
    raise ValueError(f"meal_slots.yaml does not cover {t}")


def fingerprint(items: Iterable[Mapping[str, Any]]) -> tuple:
    """Same food, same amounts → same fingerprint, regardless of item order."""
    return tuple(
        sorted(
            (
                str(i.get("food_code") or i.get("name", "")).lower(),
                float(i.get("quantity") or 0),
                str(i.get("unit", "")).lower(),
            )
            for i in items
        )
    )
