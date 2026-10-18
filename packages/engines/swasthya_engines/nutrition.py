"""Deterministic nutrition: food lookup, portion → grams, grams → nutrients.

The plate agent perceives (what is on the plate, roughly how much). Everything
numeric that follows happens here, from tables in ``data/nutrition/``. Nothing
in this module calls a model (docs/adr/0001).
"""

import json
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from enum import StrEnum
from pathlib import Path
from typing import Any

import yaml
from swasthya_shared.data import data_dir

DEFAULT_TABLE = "nutrition/indb.json"


class GramsSource(StrEnum):
    # quantity x the table's grams-per-serving for a countable unit.
    TABLE_SERVING = "table_serving"
    # The user (or label) stated grams.
    STATED = "stated"
    # The vision model's visual estimate — shown as an estimate, never as fact.
    VISUAL_ESTIMATE = "visual_estimate"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class Food:
    code: str
    name: str
    per_100g: dict[str, float | None]
    serving_unit: str | None
    serving_grams: float | None
    aliases: tuple[str, ...] = field(default=())


def _normalise(text: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", " ", text.lower()).strip()


def _aliases(name: str) -> tuple[str, ...]:
    """'Chapati/Roti (Phulka)' → ('chapati roti phulka', 'chapati', 'roti', 'phulka')."""
    parts = re.split(r"[()/,]", name)
    found = [_normalise(name)] + [_normalise(p) for p in parts]
    return tuple(dict.fromkeys(a for a in found if a))


class FoodTable:
    def __init__(self, foods: Iterable[Food], *, dataset: str) -> None:
        self.dataset = dataset
        self._by_code = {f.code: f for f in foods}

    @classmethod
    def load(cls, path: Path | None = None) -> "FoodTable":
        path = path or data_dir() / DEFAULT_TABLE
        if not path.is_file():
            raise FileNotFoundError(
                f"Nutrition table not found at {path}. Generate it with "
                "scripts/import_indb.py (see data/nutrition/README.md)."
            )
        return cls.from_dict(json.loads(path.read_text(encoding="utf-8")))

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "FoodTable":
        foods = []
        for f in raw["foods"]:
            serving = f.get("serving") or {}
            foods.append(
                Food(
                    code=f["code"],
                    name=f["name"],
                    per_100g=f["per_100g"],
                    serving_unit=serving.get("unit"),
                    serving_grams=serving.get("grams"),
                    aliases=_aliases(f["name"]),
                )
            )
        return cls(foods, dataset=raw.get("dataset", "unknown"))

    def __len__(self) -> int:
        return len(self._by_code)

    def get(self, code: str) -> Food | None:
        return self._by_code.get(code)

    def search(self, query: str, limit: int = 8, min_score: float = 0.5) -> list[Food]:
        """Rank foods by fuzzy, per-token similarity of ``query`` to any alias.

        Token-level so spelling variants match ("rajma" ~ "rajmah"), weighted
        towards covering every query token, with a mild preference for aliases
        that carry few extra words ("boiled rice" over "rice upma kheer").
        """
        q = _normalise(query)
        if not q:
            return []
        q_tokens = q.split()
        scored = []
        for food in self._by_code.values():
            best = 0.0
            for alias in food.aliases:
                if alias == q:
                    best = 2.0
                    break
                best = max(best, _alias_score(q, q_tokens, alias))
            if best >= min_score:
                scored.append((best, len(food.name), food.code, food))
        scored.sort(key=lambda s: (-s[0], s[1], s[2]))
        return [s[3] for s in scored[:limit]]


def _token_sim(a: str, b: str) -> float:
    if a == b:
        return 1.0
    ratio = SequenceMatcher(None, a, b).ratio()
    return ratio if ratio >= 0.8 else 0.0


def _alias_score(q: str, q_tokens: list[str], alias: str) -> float:
    a_tokens = alias.split()
    recall = sum(max(_token_sim(t, a) for a in a_tokens) for t in q_tokens) / len(q_tokens)
    if recall == 0:
        return 0.0
    matched = sum(1 for a in a_tokens if any(_token_sim(t, a) for t in q_tokens))
    precision = matched / len(a_tokens)
    return 0.6 * recall + 0.25 * precision + 0.15 * SequenceMatcher(None, q, alias).ratio()


@dataclass(frozen=True)
class PortionRules:
    container_units: frozenset[str]

    @classmethod
    def load(cls) -> "PortionRules":
        raw = yaml.safe_load((data_dir() / "nutrition/portions.yaml").read_text(encoding="utf-8"))
        return cls(container_units=frozenset(u.lower() for u in raw["container_units"]))


def resolve_grams(
    food: Food | None,
    *,
    quantity: float,
    unit: str,
    estimated_grams: float | None,
    rules: PortionRules,
) -> tuple[float | None, GramsSource]:
    """Turn a perceived portion into grams, preferring table-backed weights."""
    unit_n = unit.strip().lower()
    if unit_n in {"g", "gram", "grams"} and quantity > 0:
        return round(quantity, 1), GramsSource.STATED
    if (
        food is not None
        and food.serving_grams
        and food.serving_unit
        and unit_n == food.serving_unit.lower()
        and unit_n not in rules.container_units
        and quantity > 0
    ):
        return round(quantity * food.serving_grams, 1), GramsSource.TABLE_SERVING
    if estimated_grams is not None and estimated_grams > 0:
        return round(estimated_grams, 1), GramsSource.VISUAL_ESTIMATE
    return None, GramsSource.UNKNOWN


def nutrients_for(food: Food, grams: float) -> dict[str, float]:
    factor = grams / 100
    return {k: round(v * factor, 2) for k, v in food.per_100g.items() if v is not None}


def total_nutrients(parts: Sequence[dict[str, float]]) -> dict[str, float]:
    totals: dict[str, float] = {}
    for part in parts:
        for key, value in part.items():
            totals[key] = round(totals.get(key, 0.0) + value, 2)
    return totals


# --- Balanced-plate read ----------------------------------------------------


@dataclass(frozen=True)
class PlateRead:
    rule_id: str
    sentence: str
    roles_present: tuple[str, ...]


@dataclass(frozen=True)
class PlateRules:
    version: str
    roles: frozenset[str]
    rules: tuple[dict[str, Any], ...]

    @classmethod
    def load(cls) -> "PlateRules":
        raw = yaml.safe_load(
            (data_dir() / "nutrition/plate_rules.yaml").read_text(encoding="utf-8")
        )
        return cls(
            version=str(raw["version"]), roles=frozenset(raw["roles"]), rules=tuple(raw["rules"])
        )


def read_plate(items: Sequence[tuple[str, float | None]], rules: PlateRules) -> PlateRead:
    """``items`` are (role, grams-or-None). First matching rule wins."""
    unknown = {role for role, _ in items} - rules.roles
    if unknown:
        raise ValueError(f"unknown plate roles: {sorted(unknown)}")
    present = {role for role, _ in items}
    known_grams = sum(g for _, g in items if g)

    def matches(rule: dict[str, Any]) -> bool:
        if "missing_any_of" in rule and present & set(rule["missing_any_of"]):
            return False
        if "present_any_of" in rule and not present & set(rule["present_any_of"]):
            return False
        if "share_at_least" in rule:
            spec = rule["share_at_least"]
            role_grams = sum(g for r, g in items if g and r == spec["role"])
            if not known_grams or role_grams / known_grams < spec["share"]:
                return False
        return True

    for rule in rules.rules:
        if matches(rule):
            return PlateRead(rule["id"], " ".join(rule["sentence"].split()), tuple(sorted(present)))
    raise ValueError("plate_rules.yaml has no default rule")
