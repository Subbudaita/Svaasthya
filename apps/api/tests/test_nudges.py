"""JITAI end to end: fires at the moment that mattered, rarely, and is scored."""

import asyncio
import json
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from swasthya_api.nudges import NudgeService
from swasthya_api.program import build_proposal

IST = ZoneInfo("Asia/Kolkata")
START = date(2026, 10, 1)


class Gw:
    def __init__(self):
        self.sent = []

    async def send_message(self, chat_id, text, *, reply_to=None, buttons=()):
        self.sent.append(text)
        return len(self.sent)


def setup(admin_conn, user):
    body = build_proposal(
        {
            "weight_kg": 90,
            "height_cm": 175,
            "sex": "male",
            "wake_time": "06:30",
            "sleep_time": "23:30",
            "first_bite_yesterday": "08:00",
            "last_bite_yesterday": "21:40",
            "family_dinner_end": None,
            "walking": "2_to_5km",
            "diet_pattern": "vegetarian",
            "fasting_days": [],
            "goal": None,
        }
    )
    admin_conn.execute(
        "INSERT INTO program_contracts (user_id, version, phase, body, generator_version) "
        "VALUES (%s, 1, 'reset', %s, 't')",
        (user, json.dumps(body)),
    )
    admin_conn.execute("UPDATE users SET program_started_on = %s WHERE id = %s", (START, user))
    admin_conn.execute(
        "INSERT INTO health_profiles (user_id, lifestyle) VALUES (%s, %s)",
        (user, json.dumps({"energy_dip_time": "16:00"})),
    )
    admin_conn.execute(
        "INSERT INTO channel_identities (user_id, channel, external_id) "
        "VALUES (%s, 'telegram', '77')",
        (user,),
    )


def short_night(admin_conn, user, day):
    admin_conn.execute(
        "INSERT INTO pillar_days (user_id, pillar, day, result) VALUES (%s, 'sleep', %s, 'missed')",
        (user, day),
    )


def at(day, hh, mm):
    return datetime.combine(day, time(hh, mm), IST)


def test_energy_dip_nudge_fires_once_at_the_personal_risk_hour(phase1_app, make_user, admin_conn):
    _, services = phase1_app
    user = make_user()
    setup(admin_conn, user)
    day = START + timedelta(days=10)
    short_night(admin_conn, user, day - timedelta(days=1))
    gw = Gw()
    nudges = NudgeService(services, gw)
    assert asyncio.run(nudges.tick(at(day, 15, 0))) == []  # not yet the decision point
    assert asyncio.run(nudges.tick(at(day, 15, 31))) == [(user, "energy_dip", "send")]
    assert asyncio.run(nudges.tick(at(day, 15, 35))) == []  # decided once per day
    assert "16:00 dip" in gw.sent[0]

    # Proximal outcome: a protein snack 20 minutes later.
    admin_conn.execute(
        "INSERT INTO meals (user_id, eaten_at, log_method, items) VALUES (%s, %s, 'text', %s)",
        (
            user,
            at(day, 15, 51),
            json.dumps(
                [{"name": "roasted chana", "role": "protein", "quantity": 1, "unit": "cup"}]
            ),
        ),
    )
    scored = asyncio.run(nudges.score_outcomes(user, day, IST, at(day + timedelta(days=1), 7, 0)))
    assert scored == 1
    met = admin_conn.execute(
        "SELECT outcome_met FROM jitai_nudges WHERE user_id = %s", (user,)
    ).fetchone()[0]
    assert met is True


def test_hard_weekly_cap_of_two(phase1_app, make_user, admin_conn):
    _, services = phase1_app
    user = make_user()
    setup(admin_conn, user)
    gw = Gw()
    nudges = NudgeService(services, gw)
    decisions = []
    for offset in range(10, 15):  # five short nights in a row
        day = START + timedelta(days=offset)
        short_night(admin_conn, user, day - timedelta(days=1))
        decisions += [d for _, _, d in asyncio.run(nudges.tick(at(day, 15, 31)))]
    assert decisions == [
        "send",
        "send",
        "suppressed:weekly_cap",
        "suppressed:weekly_cap",
        "suppressed:weekly_cap",
    ]
    assert len(gw.sent) == 2
    logged = admin_conn.execute(
        "SELECT count(*) FROM jitai_nudges WHERE user_id = %s AND decision = 'suppressed'", (user,)
    ).fetchone()[0]
    assert logged == 3  # suppressed decisions are logged too


def test_no_nudges_while_observing(phase1_app, make_user, admin_conn):
    _, services = phase1_app
    user = make_user()
    setup(admin_conn, user)
    day = START + timedelta(days=1)  # program day 2
    short_night(admin_conn, user, START)
    gw = Gw()
    out = asyncio.run(NudgeService(services, gw).tick(at(day, 15, 31)))
    assert out == [(user, "energy_dip", "suppressed:observation")] and gw.sent == []


def test_unstructured_dinner_nudge_from_real_lapses(phase1_app, make_user, admin_conn):
    _, services = phase1_app
    user = make_user()
    setup(admin_conn, user)
    day = START + timedelta(days=12)
    for back, last in ((1, time(22, 30)), (2, time(22, 50)), (4, time(21, 0))):
        d = day - timedelta(days=back)
        for t in (time(9), last):
            admin_conn.execute(
                "INSERT INTO meals (user_id, eaten_at, log_method, items) VALUES "
                "(%s, %s, 'text', '[]')",
                (user, at(d, t.hour, t.minute)),
            )
    gw = Gw()
    out = asyncio.run(NudgeService(services, gw).tick(at(day, 19, 16)))
    assert out == [(user, "dinner_unstructured", "send")]
    assert "20:30" in gw.sent[0] and "3 of the last 7 days" in gw.sent[0]
