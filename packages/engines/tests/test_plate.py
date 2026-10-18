import json
from pathlib import Path

from swasthya_engines.nutrition import FoodTable, PlateRules, PortionRules
from swasthya_engines.plate import ItemIn, analyse, describe_portion

FIXTURE = Path(__file__).parent / "fixtures" / "foods.json"


def test_analyse_thali():
    table = FoodTable.from_dict(json.loads(FIXTURE.read_text()))
    result = analyse(
        [
            ItemIn("chapati", "T001", 2, "chapati", 70, "grain", "high"),
            ItemIn("moong dal", "T003", 1, "katori", 150, "protein", "medium"),
            ItemIn("plain curd", None, 1, "katori", 100, "dairy", "high"),
        ],
        table=table,
        portions=PortionRules.load(),
        rules=PlateRules.load(),
    )
    chapati, dal, curd = result.items
    assert (chapati.grams, chapati.grams_source) == (80.0, "table_serving")
    assert (dal.grams, dal.grams_source) == (150.0, "visual_estimate")
    assert curd.nutrients is None and result.unpriced == ("plain curd",)
    assert result.totals["protein_g"] == round(0.8 * 6 + 1.5 * 3, 2)
    assert result.rule_id == "no_vegetable"
    assert describe_portion(chapati) == "2 chapati"
    assert describe_portion(dal) == "1 katori moong dal"


def test_describe_portion_does_not_repeat_unit():
    from swasthya_engines.plate import ItemOut

    def out(name, qty, unit, confidence="high"):
        return ItemOut(name, None, None, qty, unit, None, "unknown", "grain", confidence, None)

    assert describe_portion(out("masala dosa", 1, "dosa")) == "1 masala dosa"
    assert describe_portion(out("idli", 3, "idlis")) == "3 idli"
    assert describe_portion(out("rice", 1.5, "cup")) == "1.5 cup rice"
    assert describe_portion(out("paneer", 80, "g")) == "paneer (80 g)"
    assert (
        describe_portion(out("pickle", 1, "tablespoon", "low")) == "1 tablespoon pickle (not sure)"
    )
