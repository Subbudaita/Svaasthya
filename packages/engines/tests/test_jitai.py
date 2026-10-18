from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from swasthya_engines.jitai import Gate, Tailoring, decide, outcome_met

IST = ZoneInfo("Asia/Kolkata")
OPEN_GATE = Gate(
    in_observation=False,
    is_fasting_day=False,
    sent_last_7_days=0,
    sent_today=0,
    ritual_times=(time(7), time(21, 30)),
)


def t(hh, mm, **kw):
    base = dict(
        local_now=datetime(2026, 10, 20, hh, mm, tzinfo=IST),
        window_open=time(8, 30),
        window_close=time(20, 30),
        energy_dip_time=time(16),
        breakfast_plan="moong dal cheela",
        short_sleep_last_night=False,
        meals_today=1,
        dinner_logged=False,
    )
    return Tailoring(**(base | kw))


def test_energy_dip_fires_30_min_before_the_personal_dip_after_short_sleep():
    decision, nudge = decide(t(15, 32, short_sleep_last_night=True), OPEN_GATE)
    assert decision == "send" and nudge.rule_id == "energy_dip"
    assert "16:00" in nudge.text and nudge.failure_mode == "energy_dips"


def test_no_dip_nudge_after_good_sleep_or_outside_the_decision_point():
    assert decide(t(15, 32), OPEN_GATE)[0] == "none"
    assert decide(t(14, 0, short_sleep_last_night=True), OPEN_GATE)[0] == "none"


def test_unplanned_breakfast():
    decision, nudge = decide(t(10, 0, meals_today=0, breakfast_missing_days_7d=3), OPEN_GATE)
    assert decision == "send" and "moong dal cheela" in nudge.text
    assert decide(t(10, 0, meals_today=0, breakfast_missing_days_7d=0), OPEN_GATE)[0] == "none"


def test_unstructured_dinner_uses_the_personal_lapse_clock():
    late = (time(22, 10), time(22, 40), time(23, 50))
    decision, nudge = decide(t(19, 20, late_last_bites_7d=late), OPEN_GATE)
    assert decision == "send" and nudge.rule_id == "dinner_unstructured"
    assert "3 of the last 7 days" in nudge.text and "22:40" in nudge.text
    assert decide(t(19, 20, late_last_bites_7d=late, dinner_logged=True), OPEN_GATE)[0] == "none"


def test_lapse_clock_crosses_midnight():
    assert t(1, 0, late_last_bites_7d=(time(23, 30), time(0, 30), time(0, 10))).lapse_clock == time(
        0, 10
    )


def test_caps_and_quiet_periods():
    tail = t(15, 32, short_sleep_last_night=True)
    for gate, why in [
        (Gate(False, False, 2, 0, ()), "weekly_cap"),
        (Gate(False, False, 0, 1, ()), "daily_cap"),
        (Gate(True, False, 0, 0, ()), "observation"),
        (Gate(False, False, 0, 0, (time(16, 0),)), "near_ritual"),
    ]:
        assert decide(tail, gate)[0] == f"suppressed:{why}"


def test_fasting_day_suppresses_meal_nudges():
    gate = Gate(False, True, 0, 0, ())
    assert (
        decide(t(10, 0, meals_today=0, breakfast_missing_days_7d=3), gate)[0]
        == "suppressed:fasting_day"
    )


def test_proximal_outcomes():
    sent = datetime(2026, 10, 20, 15, 30, tzinfo=IST)
    assert outcome_met(
        "protein_logged_within_120_min",
        sent_at=sent,
        meal_times=[],
        protein_meal_times=[sent + timedelta(minutes=40)],
        window_kept=None,
    )
    assert not outcome_met(
        "meal_logged_within_180_min",
        sent_at=sent,
        meal_times=[sent + timedelta(hours=5)],
        protein_meal_times=[],
        window_kept=None,
    )
    assert outcome_met(
        "window_kept_today", sent_at=sent, meal_times=[], protein_meal_times=[], window_kept=True
    )
