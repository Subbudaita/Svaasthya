"""Phase 2 acceptance: onboarding → contract → a week of rituals.

Plan "done when": both rituals fire unprompted for 7 straight days, and a
missed day is silently covered by a grace day and revealed next morning.
"""

import asyncio
import itertools
import json
import uuid
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest
from swasthya_api.rituals import RitualService
from swasthya_api.telegram import handle_update

IST = ZoneInfo("Asia/Kolkata")
TG = 5151

ANSWERS = [
    "yes",
    "Stop feeling tired at 4pm and finally lose the belly",
    "male",
    "1985",
    "175",
    "90",
    "100",
    "no",
    "no",
    "no",
    "no",
    "no",
    "no",
    "no",
    "prediabetes, acidity",
    "skip",
    "no",
    "vegetarian",
    "6:30",
    "11:30pm",
    "8",
    "9.40pm",
    "no",
    "office",
    "4pm",
    "no",
    "2 to 5km",
    "Tuesday",
    "new running shoes, a massage",
    "Anu, my sister",
    "yes",
]


class Gateway:
    def __init__(self):
        self.sent = []

    async def send_message(self, chat_id, text, *, reply_to=None, buttons=()):
        self.sent.append({"chat_id": chat_id, "text": text, "buttons": list(buttons)})
        return len(self.sent)

    async def download_file(self, file_id):
        return b""

    async def answer_callback(self, callback_id, text=None):
        pass

    async def typing(self, chat_id):
        pass


_update_ids = itertools.count(1)


def say(services, gw, text):
    n = [next(_update_ids)]
    asyncio.run(
        handle_update(
            {
                "update_id": n[0],
                "message": {
                    "message_id": n[0],
                    "from": {"id": TG},
                    "chat": {"id": TG, "type": "private"},
                    "text": text,
                },
            },
            services,
            gw,
        )
    )
    return gw.sent[-1]["text"]


@pytest.fixture
def onboarded(phase1_app, make_user, admin_conn):
    _, services = phase1_app
    user_id = make_user(timezone="Asia/Kolkata")
    admin_conn.execute(
        "INSERT INTO channel_identities (user_id, channel, external_id) VALUES (%s, 'telegram', %s)",
        (user_id, str(TG)),
    )
    gw = Gateway()
    assert "set up your program" in say(services, gw, "/onboard")
    for answer in ANSWERS:
        reply = say(services, gw, answer)
    return services, gw, user_id, reply


def test_onboarding_conversation_produces_a_contract_to_argue_with(onboarded, admin_conn):
    services, gw, user_id, proposal = onboarded
    assert "Eat between 08:30 and 20:30." in proposal  # derived, never asked
    assert "not 16:8" in proposal and "Tuesday" in proposal
    assert "Why:" in proposal and "Reply OK" in proposal
    # Nothing is the person's program until they accept.
    assert (
        admin_conn.execute(
            "SELECT count(*) FROM program_contracts WHERE user_id = %s", (user_id,)
        ).fetchone()[0]
        == 0
    )
    assert "starts today" in say(services, gw, "ok")

    body, version = admin_conn.execute(
        "SELECT body, version FROM program_contracts WHERE user_id = %s", (user_id,)
    ).fetchone()
    assert version == 1 and body["phase"] == "reset" and body["numbers_visible"] is False
    assert body["parameters"]["eating_window"] == {
        "open": "08:30",
        "close": "20:30",
        "minutes": 720,
    }
    assert body["parameters"]["baseline"]["bmi_category"] == "obese"  # 29.4 on Asian cutoffs
    assert body["parameters"]["baseline"]["waist_at_risk"] is True
    profile = admin_conn.execute(
        "SELECT lifestyle, fasting_days, diet_pattern, goal FROM health_profiles WHERE user_id = %s",
        (user_id,),
    ).fetchone()
    assert profile[0]["sleep_time"] == "23:30" and profile[1] == ["Tuesday"]
    assert profile[2] == "vegetarian" and "4pm" in profile[3]
    counts = {
        t: admin_conn.execute(
            f"SELECT count(*) FROM {t} WHERE user_id = %s", (user_id,)
        ).fetchone()[0]
        for t in ("consents", "rewards", "accountability_contacts", "consistency_tracks", "vitals")
    }
    assert counts == {
        "consents": 1,
        "rewards": 2,
        "accountability_contacts": 1,
        "consistency_tracks": 6,
        "vitals": 2,
    }


def test_food_reward_is_refused_and_asked_again(phase1_app, make_user, admin_conn):
    _, services = phase1_app
    user_id = make_user()
    admin_conn.execute(
        "INSERT INTO channel_identities (user_id, channel, external_id) "
        "VALUES (%s, 'telegram', %s)",
        (user_id, str(TG)),
    )
    gw = Gateway()
    say(services, gw, "/onboard")
    for answer in ANSWERS[: ANSWERS.index("new running shoes, a massage")]:
        say(services, gw, answer)
    reply = say(services, gw, "a pizza night, new shoes")
    assert "never food" in reply and "pizza" in reply
    assert "non-food things" in reply  # the same question again


def test_contraindicated_profile_gets_no_window(phase1_app, make_user, admin_conn):
    _, services = phase1_app
    user_id = make_user()
    admin_conn.execute(
        "INSERT INTO channel_identities (user_id, channel, external_id) "
        "VALUES (%s, 'telegram', %s)",
        (user_id, str(TG)),
    )
    gw = Gateway()
    say(services, gw, "/onboard")
    answers = list(ANSWERS)
    answers[ANSWERS.index("100") + 2] = "yes"  # insulin or sulfonylurea
    for answer in answers:
        proposal = say(services, gw, answer)
    assert "Eat between" not in proposal
    assert "no fasting window" in proposal and "doctor" in proposal


def ist(day: date, hh: int, mm: int = 0) -> datetime:
    return datetime.combine(day, time(hh, mm), IST)


def add_meal(admin_conn, user_id, when, roles=("protein", "grain")):
    items = [{"name": r, "role": r, "quantity": 1, "unit": "katori"} for r in roles]
    admin_conn.execute(
        "INSERT INTO meals (user_id, eaten_at, log_method, items) VALUES (%s, %s, 'text', %s)",
        (user_id, when, json.dumps(items)),
    )


def test_a_week_of_rituals_with_a_grace_day(onboarded, admin_conn):
    services, gw, user_id, _ = onboarded
    say(services, gw, "ok")
    start = date(2026, 10, 5)  # Monday; day 1 of the program
    admin_conn.execute("UPDATE users SET program_started_on = %s WHERE id = %s", (start, user_id))
    rituals = RitualService(services, gw)
    tick = lambda now: asyncio.run(rituals.tick(now))  # noqa: E731

    assert tick(ist(start, 7, 1)) == []  # nothing on the onboarding day itself

    briefs = {}
    for offset in range(1, 8):
        day = start + timedelta(days=offset)
        sent = tick(ist(day, 7, 1))
        assert sent == [(user_id, "morning_brief", "sent")], (day, sent)
        briefs[day] = gw.sent[-1]["text"]
        assert tick(ist(day, 7, 30)) == []  # idempotent: no second brief

        add_meal(admin_conn, user_id, ist(day, 9, 0))
        add_meal(admin_conn, user_id, ist(day, 20, 0))

        assert tick(ist(day, 21, 31)) == [(user_id, "evening_checkin", "sent")]
        checkin = gw.sent[-1]
        assert "one question" in checkin["text"]
        assert len(checkin["buttons"]) == 10  # five pillars x ✓/✗ (mind = answering)
        walked = day != start + timedelta(days=5)  # one honest miss
        asyncio.run(rituals.record_report(user_id, day, "movement", walked))
        asyncio.run(
            rituals.record_causal_answer(user_id, "Long meeting, ate late.", now=ist(day, 21, 45))
        )

    runs = admin_conn.execute(
        "SELECT status, count(*) FROM ritual_runs WHERE user_id = %s GROUP BY status", (user_id,)
    ).fetchall()
    assert runs == [("sent", 14)]  # 7 days x 2 rituals, all delivered

    # Days 1-3 only observe; scoring starts on day 4.
    assert "only watch" in briefs[start + timedelta(days=1)]
    assert "only watch" not in briefs[start + timedelta(days=4)]
    # The miss on day 6 was covered silently and revealed the next morning — not before.
    assert "grace day" not in briefs[start + timedelta(days=5)]
    assert "grace day" in briefs[start + timedelta(days=6)]
    assert "movement" in briefs[start + timedelta(days=6)]
    assert "grace day" not in briefs[start + timedelta(days=7)]  # revealed once
    assert "Kitchen opens 08:30, closes 20:30." in briefs[start + timedelta(days=4)]
    assert "kcal" not in "".join(briefs.values())

    results = dict(
        admin_conn.execute(
            "SELECT day, result FROM pillar_days WHERE user_id = %s AND pillar = 'movement'",
            (user_id,),
        ).fetchall()
    )
    assert results[start + timedelta(days=5)] == "grace"
    assert results[start + timedelta(days=1)] == "observation"
    run = admin_conn.execute(
        "SELECT current_run_days FROM consistency_tracks WHERE user_id = %s AND pillar = 'movement'",
        (user_id,),
    ).fetchone()[0]
    assert run == 3  # days 4, 5 and 7 met (closed out through day 7); day 6 covered, not counted
    window_days = dict(
        admin_conn.execute(
            "SELECT day, result FROM pillar_days WHERE user_id = %s AND pillar = 'eating_window'",
            (user_id,),
        ).fetchall()
    )
    assert window_days[start + timedelta(days=4)] == "met"  # from meal times, not memory
    facts = admin_conn.execute(
        "SELECT count(*) FROM memory_facts WHERE user_id = %s AND category = 'causal_answer'",
        (user_id,),
    ).fetchone()[0]
    assert facts == 7
    audits = admin_conn.execute(
        "SELECT contract_clause FROM audit_log WHERE user_id = %s AND action = 'ritual.morning_brief'",
        (user_id,),
    ).fetchall()
    assert len(audits) == 7 and all("eating_window.target" in a[0] for a in audits)


def test_undelivered_ritual_is_a_logged_incident(onboarded, admin_conn):
    services, gw, user_id, _ = onboarded
    say(services, gw, "ok")
    start = date(2026, 10, 5)
    admin_conn.execute("UPDATE users SET program_started_on = %s WHERE id = %s", (start, user_id))
    rituals = RitualService(services, gw)
    # The worker was down all day; at 23:59 both rituals are past catch-up.
    out = asyncio.run(rituals.tick(ist(start + timedelta(days=1), 23, 59)))
    assert sorted(s for _, _, s in out) == ["missed", "missed"]
    assert len(gw.sent) == len([m for m in gw.sent])  # nothing sent late at night


def test_reward_is_paid_retroactively_at_a_milestone(onboarded, admin_conn):
    services, gw, user_id, _ = onboarded
    say(services, gw, "ok")
    start = date(2026, 9, 1)
    admin_conn.execute("UPDATE users SET program_started_on = %s WHERE id = %s", (start, user_id))
    admin_conn.execute(
        "UPDATE consistency_tracks SET current_run_days = 13, longest_run_days = 13, "
        "last_counted_on = '2026-10-05' WHERE user_id = %s AND pillar = 'movement'",
        (user_id,),
    )
    rituals = RitualService(services, gw)
    asyncio.run(rituals.record_report(user_id, date(2026, 10, 6), "movement", True))
    asyncio.run(rituals.tick(ist(date(2026, 10, 7), 7, 5)))
    brief = gw.sent[-1]["text"]
    assert "14 days in a row" in brief and "new running shoes" in brief
    assert "someone who walks after meals" in brief
    status = admin_conn.execute(
        "SELECT status, paid_for->>'pillar' FROM rewards WHERE user_id = %s ORDER BY created_at",
        (user_id,),
    ).fetchall()
    assert status[0] == ("paid", "movement") and status[1][0] == "available"


def test_checkin_taps_and_time_change(onboarded, admin_conn):
    services, gw, user_id, _ = onboarded
    say(services, gw, "ok")
    asyncio.run(
        handle_update(
            {
                "update_id": 999,
                "callback_query": {
                    "id": "x",
                    "from": {"id": TG},
                    "data": "ci:2026-10-06:sleep:0",
                    "message": {"message_id": 1, "chat": {"id": TG, "type": "private"}},
                },
            },
            services,
            gw,
        )
    )
    reports = admin_conn.execute(
        "SELECT pillar_reports FROM daily_logs WHERE user_id = %s AND log_date = '2026-10-06'",
        (user_id,),
    ).fetchone()[0]
    assert reports == {"sleep": False}
    assert "06:45" in say(services, gw, "/time morning 6:45")
    assert admin_conn.execute(
        "SELECT morning_brief_at FROM users WHERE id = %s", (user_id,)
    ).fetchone()[0] == time(6, 45)


def test_program_endpoint_hides_unpaid_rewards(phase1_app, make_token, admin_conn):
    client, services = phase1_app
    user_id = uuid.uuid4()
    headers = {"Authorization": f"Bearer {make_token(user_id)}"}
    client.get("/v1/me", headers=headers)
    assert client.get("/v1/program", headers=headers).status_code == 404
