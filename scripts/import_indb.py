"""Build data/nutrition/indb.json from the Indian Nutrient Databank (INDB).

INDB (Vijayakumar, Jaacks et al.) publishes nutrient values for 1,014 cooked
Indian recipes, derived from ICMR-NIN IFCT 2017. Its repository has no LICENSE
file, so Swasthya does not redistribute it: this script converts a local copy.

    git clone --depth 1 https://github.com/lindsayjaacks/indian-nutrient-databank-indb- /tmp/indb
    uv run --group data python scripts/import_indb.py /tmp/indb/INDB.xlsx

Values are copied, never computed, except one derived field: grams per
serving = per-serving kcal / per-100 g kcal x 100, which is exactly how INDB
itself scaled the serving columns.
"""

import argparse
import json
import sys
from datetime import date
from pathlib import Path

import openpyxl

NUTRIENTS = (
    "energy_kcal",
    "protein_g",
    "carb_g",
    "fat_g",
    "fibre_g",
    "freesugar_g",
    "sodium_mg",
    "calcium_mg",
    "iron_mg",
)
OUT = Path(__file__).resolve().parents[1] / "data" / "nutrition" / "indb.json"


def _num(value: object) -> float | None:
    if value is None or value == "":
        return None
    try:
        return round(float(value), 3)
    except (TypeError, ValueError):
        return None


def convert(xlsx: Path) -> dict:
    sheet = openpyxl.load_workbook(xlsx, read_only=True, data_only=True).worksheets[0]
    rows = sheet.iter_rows(values_only=True)
    header = [str(h) for h in next(rows)]
    col = {name: i for i, name in enumerate(header)}
    missing = [n for n in ("food_code", "food_name", *NUTRIENTS) if n not in col]
    if missing:
        raise SystemExit(f"unexpected INDB layout, missing columns: {missing}")

    foods = []
    for row in rows:
        code, name = row[col["food_code"]], row[col["food_name"]]
        if not code or not name:
            continue
        per_100g = {n: _num(row[col[n]]) for n in NUTRIENTS}
        kcal_100 = per_100g["energy_kcal"]
        kcal_serving = _num(row[col["unit_serving_energy_kcal"]])
        unit = (row[col["servings_unit"]] or "").strip() or None
        grams = (
            round(kcal_serving / kcal_100 * 100, 1) if unit and kcal_serving and kcal_100 else None
        )
        foods.append(
            {
                "code": str(code).strip(),
                "name": " ".join(str(name).split()),
                "source": row[col["primarysource"]],
                "per_100g": per_100g,
                "serving": {"unit": unit, "grams": grams} if unit else None,
            }
        )
    return {
        "dataset": "INDB",
        "citation": (
            "Vijayakumar A, et al. Indian Nutrient Databank (INDB). "
            "https://github.com/lindsayjaacks/indian-nutrient-databank-indb-"
        ),
        "converted_on": date.today().isoformat(),
        "basis": "per 100 g edible portion, cooked recipe",
        "foods": foods,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("xlsx", type=Path, help="path to INDB.xlsx")
    parser.add_argument("--out", type=Path, default=OUT)
    args = parser.parse_args()
    table = convert(args.xlsx)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(table, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"wrote {len(table['foods'])} foods to {args.out}", file=sys.stderr)


if __name__ == "__main__":
    main()
