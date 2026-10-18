from datetime import time

import pytest
from swasthya_engines.contraindications import screen
from swasthya_engines.eating_window import Day, derive
from swasthya_engines.program_contract import (
    ProgramInputs,
    build,
    clause,
    summary_text,
    window_of,
)

DAY = Day(time(6, 30), time(23, 30), time(8), time(21, 40))


def inputs(**kw):
    base = dict(
        weight_kg=90,
        sleep_time=time(23, 30),
        walking="2_to_5km",
        diet_pattern="vegetarian",
        fasting_days=("Tuesday",),
        window=derive(DAY),
    )
    return ProgramInputs(**(base | kw))


def test_reset_contract_from_profile():
    c = build("reset", inputs(), screen({}, bmi_category="obese"))
    assert c["phase"] == "reset" and c["numbers_visible"] is False
    assert c["observation_days"] == 3
    assert clause(c, "eating_window.target")["text"] == "Eat between 08:30 and 20:30."
    assert clause(c, "eating_window.regular_meals") is None
    assert window_of(c).length == 720
    assert c["parameters"]["protein_target_g"] is None  # no numbers in Reset
    assert "Tuesday" in summary_text(c)
    for cl in c["clauses"]:
        assert cl["why"], cl["id"]  # every clause says why it applies to you


@pytest.mark.parametrize(
    "answer",
    [
        "pregnant_or_breastfeeding",
        "type_1_diabetes",
        "insulin_or_sulfonylurea",
        "eating_disorder_history",
    ],
)
def test_hard_gate_removes_every_window_recommendation(answer):
    s = screen({answer: True}, bmi_category="normal")
    c = build("reset", inputs(window=None), s)
    assert c["parameters"]["eating_window"] is None
    assert clause(c, "eating_window.target") is None
    assert clause(c, "eating_window.regular_meals") is not None
    assert any("eating-window" in p for p in c["prohibitions"])


def test_underweight_is_gated_from_measurements():
    s = screen({}, bmi_category="underweight")
    assert s.has("no_time_restricted_eating") and s.has("no_calorie_deficit")


def test_kidney_disease_blocks_elevated_protein_in_rebuild():
    normal = build("rebuild", inputs(), screen({}, bmi_category="obese"))
    assert normal["parameters"]["protein_target_g"] == 108  # 1.2 g/kg x 90
    ckd = build("rebuild", inputs(), screen({"kidney_disease": True}, bmi_category="obese"))
    assert ckd["parameters"]["protein_target_g"] is None
    assert clause(ckd, "nutrition.protein_standard") is not None
    assert ckd["refer_first"] is True
    assert "doctor" in summary_text(ckd)


def test_window_required_unless_gated():
    with pytest.raises(ValueError):
        build("reset", inputs(window=None), screen({}, bmi_category="obese"))


def test_no_personal_value_in_templates():
    """Templates only hold placeholders; values come from the person's answers."""
    a = build("reset", inputs(sleep_time=time(22, 0)), screen({}, bmi_category="obese"))
    assert "22:00" in clause(a, "sleep.consistent_bedtime")["text"]
