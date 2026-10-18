"""BMI and waist classification against thresholds loaded from ``data/``.

No threshold is written inline: the cutoffs are population data, and shipping
the wrong population's numbers tells an at-risk user they are fine.
"""

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

import yaml
from swasthya_shared.data import data_dir

DEFAULT_TABLE = "anthropometry/south_asian.yaml"


class Sex(StrEnum):
    MALE = "male"
    FEMALE = "female"


@dataclass(frozen=True)
class BmiBand:
    category: str
    min: float | None
    max: float | None

    def contains(self, bmi: float) -> bool:
        return (self.min is None or bmi >= self.min) and (self.max is None or bmi < self.max)


@dataclass(frozen=True)
class AnthropometryTable:
    version: str
    population: str
    bmi_bands: tuple[BmiBand, ...]
    waist_cm_at_risk: dict[Sex, float]

    @classmethod
    def load(cls, relative_path: str = DEFAULT_TABLE) -> "AnthropometryTable":
        raw = yaml.safe_load(Path(data_dir(), relative_path).read_text(encoding="utf-8"))
        return cls(
            version=str(raw["version"]),
            population=raw["population"],
            bmi_bands=tuple(BmiBand(**band) for band in raw["bmi_kg_m2"]),
            waist_cm_at_risk={Sex(k): float(v) for k, v in raw["waist_cm_at_risk"].items()},
        )


def bmi(weight_kg: float, height_cm: float) -> float:
    if weight_kg <= 0 or height_cm <= 0:
        raise ValueError("weight and height must be positive")
    height_m = height_cm / 100
    return weight_kg / (height_m * height_m)


def classify_bmi(value: float, table: AnthropometryTable) -> str:
    for band in table.bmi_bands:
        if band.contains(value):
            return band.category
    raise ValueError(f"BMI {value} is not covered by table {table.population}@{table.version}")


def waist_at_risk(waist_cm: float, sex: Sex, table: AnthropometryTable) -> bool:
    return waist_cm >= table.waist_cm_at_risk[sex]
