from datetime import date, time

import pytest
from swasthya_engines.onboarding import AnswerError, Script, parse_answer
from swasthya_engines.rituals import (
    BriefFacts,
    OwnBreakfast,
    choose_breakfast,
    choose_question,
    compose_brief,
)


def test_script_skips_inapplicable_steps():
    script = Script.load()
    answers = {"consent": True, "sex": "male", "accountability": None}  # skipped naming anyone
    applicable = {s.id for s in script.steps if script.applies(s, answers)}
    assert "pregnant_or_breastfeeding" not in applicable
    assert "accountability_agreed" not in applicable  # only after naming someone
    named = answers | {"sex": "female", "accountability": "Anu, my sister"}
    applicable = {s.id for s in script.steps if script.applies(s, named)}
    assert {"pregnant_or_breastfeeding", "accountability_agreed"} <= applicable


def test_no_step_asks_for_a_target():
    for step in Script.load().steps:
        assert "window do you want" not in step.prompt.lower()
        assert "target" not in step.prompt.lower()


@pytest.mark.parametrize(
    ("step_id", "raw", "value"),
    [
        ("consent", "yes", True),
        ("sex", "Male", "male"),
        ("height_cm", "172", 172),
        ("weight_kg", "89,5", 89.5),
        ("wake_time", "6.30am", "06:30"),
        ("family_dinner_end", "no", None),
        ("fasting_days", "Tuesday, Ekadashi", ["Tuesday", "Ekadashi"]),
        ("fasting_days", "none", []),
        ("waist_cm", "skip", None),
        ("walking", "2 to 5km", "2_to_5km"),
    ],
)
def test_parse_answers(step_id, raw, value):
    step = next(s for s in Script.load().steps if s.id == step_id)
    assert parse_answer(step, raw) == value


@pytest.mark.parametrize(
    ("step_id", "raw"),
    [("consent", "maybe"), ("height_cm", "6ft"), ("weight_kg", "900"), ("rewards", "none")],
)
def test_parse_rejects(step_id, raw):
    step = next(s for s in Script.load().steps if s.id == step_id)
    with pytest.raises(AnswerError):
        parse_answer(step, raw)


def test_breakfast_prefers_own_protein_breakfast():
    own = [OwnBreakfast("2 toast", False, 9), OwnBreakfast("besan cheela", True, 3)]
    assert choose_breakfast(own, "vegetarian", date(2026, 10, 1)) == ("besan cheela", "yours")
    label, source = choose_breakfast([], "vegan", date(2026, 10, 1))
    assert source == "suggested" and "paneer" not in label and "egg" not in label


def test_one_causal_question():
    assert (
        choose_question({"last_bite_after_window": True, "energy_low": True})[0] == "late_last_bite"
    )
    assert choose_question({})[0] == "default"


def test_brief_reveals_grace_and_never_shows_calories():
    text = compose_brief(
        BriefFacts(
            day=date(2026, 10, 19),
            phase="reset",
            program_day=8,
            observation_days=3,
            window=(time(8, 30), time(20, 30)),
            is_fasting_day=False,
            breakfast=("moong dal cheela with curd", "suggested"),
            yesterday_window="missed",
            yesterday_last_bite=time(21, 50),
            grace_reveals=["your after-meal walk"],
            recognitions=[],
            clause_lines=["A 10-minute walk after your biggest meal."],
        )
    )
    assert "grace day" in text and "Kitchen opens 08:30, closes 20:30." in text
    assert "kcal" not in text and "Breakfast, decided: moong dal cheela" in text
