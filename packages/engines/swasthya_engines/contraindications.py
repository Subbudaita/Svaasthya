"""Contraindication screen → permanent flags (docs/PLAN.md §5). Deterministic."""

from collections.abc import Mapping
from dataclasses import dataclass
from functools import cache
from typing import Any

import yaml
from swasthya_shared.data import data_dir


@dataclass(frozen=True)
class Screen:
    flags: frozenset[str]
    reasons: tuple[tuple[str, str], ...]  # (screen key, reason)
    prohibitions: tuple[str, ...]

    def has(self, flag: str) -> bool:
        return flag in self.flags


@cache
def _rules() -> dict[str, Any]:
    return yaml.safe_load(
        (data_dir() / "safety/contraindications.yaml").read_text(encoding="utf-8")
    )


def screen(answers: Mapping[str, Any], *, bmi_category: str | None) -> Screen:
    rules = _rules()
    flags: set[str] = set()
    reasons: list[tuple[str, str]] = []
    for key, rule in rules["screen"].items():
        if answers.get(key) is True:
            flags.update(rule["flags"])
            reasons.append((key, rule["reason"]))
    if bmi_category == "underweight":
        flags.update(rules["underweight"]["flags"])
        reasons.append(("underweight", rules["underweight"]["reason"]))
    prohibitions = tuple(rules["prohibitions"][f] for f in sorted(flags))
    return Screen(frozenset(flags), tuple(reasons), prohibitions)
