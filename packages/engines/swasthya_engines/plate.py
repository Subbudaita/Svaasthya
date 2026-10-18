"""Plate analysis: perceived items → grams → nutrients → one-sentence read.

Pure and deterministic. Inputs are what perception saw; every number in the
output comes from ``data/nutrition/`` tables or is explicitly marked as the
model's visual estimate.
"""

from collections.abc import Sequence
from dataclasses import asdict, dataclass

from swasthya_engines.nutrition import (
    FoodTable,
    GramsSource,
    PlateRules,
    PortionRules,
    nutrients_for,
    read_plate,
    resolve_grams,
    total_nutrients,
)


@dataclass(frozen=True)
class ItemIn:
    name: str
    food_code: str | None
    quantity: float
    unit: str
    estimated_grams: float | None
    role: str
    confidence: str


@dataclass(frozen=True)
class ItemOut:
    name: str
    food_code: str | None
    food_name: str | None
    quantity: float
    unit: str
    grams: float | None
    grams_source: str
    role: str
    confidence: str
    nutrients: dict[str, float] | None


@dataclass(frozen=True)
class PlateAnalysis:
    items: tuple[ItemOut, ...]
    totals: dict[str, float]
    unpriced: tuple[str, ...]
    rule_id: str
    sentence: str
    table: str

    def to_dict(self) -> dict:
        return asdict(self)


def analyse(
    items: Sequence[ItemIn],
    *,
    table: FoodTable,
    portions: PortionRules,
    rules: PlateRules,
) -> PlateAnalysis:
    out: list[ItemOut] = []
    for item in items:
        food = table.get(item.food_code) if item.food_code else None
        grams, source = resolve_grams(
            food,
            quantity=item.quantity,
            unit=item.unit,
            estimated_grams=item.estimated_grams,
            rules=portions,
        )
        nutrients = nutrients_for(food, grams) if food is not None and grams else None
        out.append(
            ItemOut(
                name=item.name,
                food_code=food.code if food else None,
                food_name=food.name if food else None,
                quantity=item.quantity,
                unit=item.unit,
                grams=grams,
                grams_source=source.value,
                role=item.role,
                confidence=item.confidence,
                nutrients=nutrients,
            )
        )
    read = read_plate([(i.role, i.grams) for i in out], rules)
    return PlateAnalysis(
        items=tuple(out),
        totals=total_nutrients([i.nutrients for i in out if i.nutrients]),
        unpriced=tuple(i.name for i in out if i.nutrients is None),
        rule_id=read.rule_id,
        sentence=read.sentence,
        table=table.dataset,
    )


def describe_portion(item: ItemOut) -> str:
    """'2 chapati', '1 katori moong dal' — natural units, never calories."""
    qty = item.quantity
    qty_text = str(int(qty)) if float(qty).is_integer() else f"{qty:g}"
    unit = item.unit.strip()
    name = item.name.strip()
    unit_l, name_words = unit.lower(), name.lower().split()
    # "2 chapati", "1 masala dosa": the unit is already in the name.
    if unit_l in {"piece", "pieces"} or unit_l in name_words or unit_l.rstrip("s") in name_words:
        text = f"{qty_text} {name}"
    elif unit.lower() in {"g", "gram", "grams"}:
        text = f"{name} ({qty_text} g)"
    else:
        text = f"{qty_text} {unit} {name}"
    if item.confidence == "low":
        text += " (not sure)"
    return text


def estimate_flagged(item: ItemOut) -> bool:
    return item.grams_source == GramsSource.VISUAL_ESTIMATE.value
