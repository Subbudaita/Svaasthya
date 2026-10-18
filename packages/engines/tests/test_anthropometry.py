import pytest
from swasthya_engines.anthropometry import (
    AnthropometryTable,
    Sex,
    bmi,
    classify_bmi,
    waist_at_risk,
)


@pytest.fixture(scope="module")
def table() -> AnthropometryTable:
    return AnthropometryTable.load()


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (18.4, "underweight"),
        (18.5, "normal"),
        (22.9, "normal"),
        (23.0, "overweight"),  # WHO would say "normal" here
        (24.9, "overweight"),
        (25.0, "obese"),  # WHO would say "overweight" here
        (29.9, "obese"),
    ],
)
def test_south_asian_bmi_cutoffs(table, value, expected):
    assert classify_bmi(value, table) == expected


def test_waist_threshold_men(table):
    assert waist_at_risk(90, Sex.MALE, table)
    assert not waist_at_risk(89.9, Sex.MALE, table)


def test_bmi_formula():
    assert bmi(81, 180) == pytest.approx(25.0)
    with pytest.raises(ValueError):
        bmi(0, 170)


def test_thresholds_come_from_data_not_code(tmp_path, monkeypatch):
    """Editing the data file changes the result — nothing is hard-coded."""
    (tmp_path / "anthropometry").mkdir()
    (tmp_path / "anthropometry" / "south_asian.yaml").write_text(
        """
version: test
population: test
bmi_kg_m2:
  - {category: low, min: null, max: 40}
  - {category: high, min: 40, max: null}
waist_cm_at_risk: {male: 200, female: 200}
"""
    )
    monkeypatch.setenv("SWASTHYA_DATA_DIR", str(tmp_path))
    custom = AnthropometryTable.load()
    assert classify_bmi(30, custom) == "low"
    assert not waist_at_risk(150, Sex.MALE, custom)
