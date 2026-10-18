from datetime import date, time, timedelta

import pytest
from swasthya_engines.clock import parse_time
from swasthya_engines.eating_window import (
    Action,
    Day,
    DayRecord,
    Window,
    derive,
    kept,
    next_step,
)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("7", time(7)),
        ("7:30", time(7, 30)),
        ("7.30am", time(7, 30)),
        ("10 pm", time(22)),
        ("12am", time(0)),
        ("12 pm", time(12)),
        ("19:45", time(19, 45)),
    ],
)
def test_parse_time(text, expected):
    assert parse_time(text) == expected


@pytest.mark.parametrize("bad", ["25:00", "7:75", "13pm", "noon", ""])
def test_parse_time_rejects(bad):
    with pytest.raises(ValueError):
        parse_time(bad)


def test_plan_example_13h40_becomes_12h_anchored_on_sleep():
    # Wake 06:30, sleep 23:30, ate 08:00 → 21:40 yesterday (13 h 40).
    p = derive(Day(time(6, 30), time(23, 30), time(8, 0), time(21, 40)))
    assert p.observed_minutes == 13 * 60 + 40
    assert p.window.close == time(20, 30)  # 3 h before sleep
    assert p.window.length == 12 * 60  # 12 h, not 16:8
    assert p.window.open == time(8, 30)
    assert p.conflict is None
    assert "not 16:8" in p.why


def test_large_baseline_is_not_cut_more_than_two_hours():
    p = derive(Day(time(6), time(23), time(7), time(22)))  # 15 h observed
    assert p.window.length == 13 * 60


def test_family_dinner_is_the_binding_constraint():
    p = derive(Day(time(7), time(23, 0), time(8), time(22), family_dinner_end=time(21, 30)))
    assert p.window.close == time(21, 30)
    assert p.conflict and "Family dinner" in p.conflict


def test_sleep_after_midnight():
    p = derive(Day(time(8), time(0, 30), time(9), time(23)))
    assert p.window.close == time(21, 30)


def test_kept_with_tolerance():
    w = Window(time(8, 30), time(20, 30))
    assert kept(w, time(8, 20), time(20, 40)) is True  # inside the 15-min tolerance
    assert kept(w, time(8, 30), time(21, 0)) is False
    assert kept(w, None, time(20)) is None


def records(n, kept_=True, energy=3, sleep=420, start=date(2026, 10, 1)):
    return [DayRecord(start + timedelta(days=i), kept_, energy, sleep) for i in range(n)]


W = Window(time(8, 30), time(20, 30))


def test_holds_until_hold_period():
    d = next_step(
        W, since_change=records(5), consecutive_failures=0, phase="reset", sleep_time=time(23, 30)
    )
    assert d.action is Action.HOLD


def test_reset_floor_is_12h_so_no_narrowing_in_reset():
    d = next_step(
        W, since_change=records(8), consecutive_failures=0, phase="reset", sleep_time=time(23, 30)
    )
    assert d.action is Action.HOLD and "destination" in d.reason


def test_narrows_one_end_when_all_gates_pass():
    d = next_step(
        W, since_change=records(8), consecutive_failures=0, phase="rebuild", sleep_time=time(23, 30)
    )
    assert d.action is Action.NARROW
    assert d.window.close == W.close and d.window.open == time(9, 0)  # 30 min (100% kept)


def test_never_below_rebuild_floor():
    w = Window(time(10, 15), time(20, 30))  # 10h15
    d = next_step(
        w, since_change=records(8), consecutive_failures=0, phase="rebuild", sleep_time=time(23, 30)
    )
    assert d.window.length == 600


def test_energy_falling_blocks_narrowing():
    recs = records(4, energy=4) + records(4, energy=2, start=date(2026, 10, 5))
    d = next_step(
        W, since_change=recs, consecutive_failures=0, phase="rebuild", sleep_time=time(23, 30)
    )
    assert d.action is Action.HOLD and "energy" in d.reason


def test_two_failures_mean_review_not_pressure():
    d = next_step(
        W,
        since_change=records(8, kept_=False),
        consecutive_failures=1,
        phase="rebuild",
        sleep_time=time(23, 30),
    )
    assert d.action is Action.REVIEW and "isn't the problem" in d.reason


def test_widen_back_after_long_failed_hold():
    d = next_step(
        W,
        since_change=records(10, kept_=False),
        consecutive_failures=0,
        phase="rebuild",
        sleep_time=time(23, 30),
    )
    assert d.action is Action.WIDEN and d.window.length > W.length


def test_exempt_days_do_not_count_against_adherence():
    recs = records(6) + [DayRecord(date(2026, 10, 7), None), DayRecord(date(2026, 10, 8), None)]
    d = next_step(
        W, since_change=recs, consecutive_failures=0, phase="rebuild", sleep_time=time(23, 30)
    )
    assert d.action is Action.NARROW
