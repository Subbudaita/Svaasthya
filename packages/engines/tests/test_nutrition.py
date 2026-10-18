import json
from pathlib import Path

import pytest
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

FIXTURE = Path(__file__).parent / "fixtures" / "foods.json"


@pytest.fixture(scope="module")
def table() -> FoodTable:
    return FoodTable.from_dict(json.loads(FIXTURE.read_text()))


@pytest.fixture(scope="module")
def portions() -> PortionRules:
    return PortionRules.load()


@pytest.fixture(scope="module")
def plate_rules() -> PlateRules:
    return PlateRules.load()


@pytest.mark.parametrize(
    ("query", "code"),
    [
        ("roti", "T001"),
        ("chapati", "T001"),
        ("rice", "T002"),
        ("moong dal", "T003"),
        ("dhuli moong ki dal", "T003"),
        ("masala dosa", "T005"),
        ("sambar", "T006"),
    ],
)
def test_search_finds_by_name_or_alias(table, query, code):
    assert table.search(query)[0].code == code


def test_search_rejects_nonsense(table):
    assert table.search("xqzv pizza") == []
    assert table.search("   ") == []


def test_countable_serving_uses_table_weight(table, portions):
    grams, source = resolve_grams(
        table.get("T001"), quantity=2, unit="chapati", estimated_grams=70, rules=portions
    )
    assert (grams, source) == (80.0, GramsSource.TABLE_SERVING)


def test_container_serving_falls_back_to_visual_estimate(table, portions):
    # An INDB "bowl" is not a household katori: don't multiply by it.
    grams, source = resolve_grams(
        table.get("T003"), quantity=1, unit="bowl", estimated_grams=150, rules=portions
    )
    assert (grams, source) == (150.0, GramsSource.VISUAL_ESTIMATE)


def test_stated_grams_win(table, portions):
    assert resolve_grams(
        table.get("T002"), quantity=180, unit="g", estimated_grams=250, rules=portions
    ) == (180.0, GramsSource.STATED)


def test_unknown_portion(portions):
    assert resolve_grams(None, quantity=1, unit="piece", estimated_grams=None, rules=portions) == (
        None,
        GramsSource.UNKNOWN,
    )


def test_nutrients_scale_and_skip_missing(table):
    dosa = nutrients_for(table.get("T004"), 50)
    assert dosa["protein_g"] == 3.5
    assert "fibre_g" not in dosa  # null in the table stays absent, never zero
    totals = total_nutrients([nutrients_for(table.get("T001"), 80), dosa])
    assert totals["protein_g"] == pytest.approx(8.3)


@pytest.mark.parametrize(
    ("items", "rule"),
    [
        ([("grain", 120)], "no_protein"),
        ([("grain", 120), ("protein", 150)], "no_vegetable"),
        ([("grain", 400), ("protein", 100), ("vegetable", 100)], "grain_heavy"),
        ([("grain", 100), ("dairy", 100), ("vegetable", 100), ("fried", 50)], "fried_or_sweet"),
        ([("grain", 100), ("protein", 150), ("vegetable", 100)], "balanced"),
    ],
)
def test_plate_read_rules(plate_rules, items, rule):
    read = read_plate(items, plate_rules)
    assert read.rule_id == rule
    assert not any(ch.isdigit() for ch in read.sentence)  # no numbers in the read


def test_plate_read_rejects_unknown_roles(plate_rules):
    with pytest.raises(ValueError):
        read_plate([("carbs", 100)], plate_rules)


def test_real_table_loads_if_generated():
    """Exercises the converted INDB file when present (it is not committed)."""
    try:
        real = FoodTable.load()
    except FileNotFoundError:
        pytest.skip("data/nutrition/indb.json not generated (scripts/import_indb.py)")
    assert len(real) > 1000
    chapati = real.search("chapati")[0]
    assert chapati.serving_unit == "chapati" and chapati.serving_grams
