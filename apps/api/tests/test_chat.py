"""Phase 3 acceptance: the coach answers from the person's own last three days."""

import asyncio
import json
import uuid
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from swasthya_api.chat import ChatService
from swasthya_api.program import build_proposal
from swasthya_api.telegram import handle_update

IST = ZoneInfo("Asia/Kolkata")
TODAY = date(2026, 10, 18)
NOW = datetime.combine(TODAY, time(16, 10), IST)

ANSWERS = {
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
    "fasting_days": ["Tuesday"],
    "goal": "Stop the 4pm crash",
    "energy_dip_time": "16:00",
}


def seed_program(admin_conn, user_id, started=TODAY - timedelta(days=10)):
    contract = build_proposal(ANSWERS)
    admin_conn.execute(
        "INSERT INTO program_contracts (user_id, version, phase, body, generator_version) "
        "VALUES (%s, 1, 'reset', %s, 'contract-engine/1')",
        (user_id, json.dumps(contract)),
    )
    admin_conn.execute("UPDATE users SET program_started_on = %s WHERE id = %s", (started, user_id))
    admin_conn.execute(
        "INSERT INTO health_profiles (user_id, diet_pattern, lifestyle) VALUES (%s, 'vegetarian', %s)",
        (user_id, json.dumps({"energy_dip_time": "16:00", "sleep_time": "23:30"})),
    )
    return contract


def meal(admin_conn, user_id, when, roles=("protein", "grain"), name="dal"):
    items = [
        {"name": name if r == "protein" else r, "role": r, "quantity": 1, "unit": "katori"}
        for r in roles
    ]
    admin_conn.execute(
        "INSERT INTO meals (user_id, eaten_at, log_method, items) VALUES (%s, %s, 'text', %s)",
        (user_id, when, json.dumps(items)),
    )


def seed_three_days(admin_conn, user_id):
    for back, last in ((1, time(22, 40)), (2, time(22, 55)), (3, time(20, 10))):
        d = TODAY - timedelta(days=back)
        meal(admin_conn, user_id, datetime.combine(d, time(8, 45), IST))
        meal(admin_conn, user_id, datetime.combine(d, last, IST), roles=("grain",), name="maggi")
        admin_conn.execute(
            "INSERT INTO pillar_days (user_id, pillar, day, result) VALUES (%s, 'sleep', %s, %s)",
            (user_id, d, "missed" if back < 3 else "met"),
        )
    admin_conn.execute(
        "INSERT INTO ritual_runs (user_id, ritual, local_date, status, payload, answer) "
        "VALUES (%s, 'evening_checkin', %s, 'sent', %s, 'Work call ran till 10, ate Maggi after')",
        (
            user_id,
            TODAY - timedelta(days=1),
            json.dumps(
                {"question": "Your last bite today was after your window. What pushed it late?"}
            ),
        ),
    )


def run(coro):
    return asyncio.run(coro)


def test_why_am_i_so_tired_today_cites_the_last_three_days(
    phase1_app, make_user, admin_conn, model
):
    _, services = phase1_app
    user = make_user()
    seed_program(admin_conn, user)
    seed_three_days(admin_conn, user)
    yesterday = (TODAY - timedelta(days=1)).isoformat()
    model.will_route("insight")
    model.will_answer(
        "Simplest explanation first: two late nights in a row. Your last bite was 22:40 "
        "yesterday and 22:55 the day before, and sleep was missed both nights. I'm fairly sure "
        "it's sleep, not food. If you close by 20:30 for a few days and the 4pm dip stays, "
        "I'm wrong.",
        clauses=["eating_window.target", "sleep.consistent_bedtime"],
        days=[yesterday, (TODAY - timedelta(days=2)).isoformat()],
        confidence="medium",
    )

    r = run(
        ChatService(services).reply(user, "why am I so tired today?", channel="telegram", now=NOW)
    )
    assert r.specialist == "insight" and "22:40" in r.text
    assert r.cited_clauses == ["eating_window.target", "sleep.consistent_bedtime"]
    assert yesterday in r.cited_days

    specialist_call = model.calls[-1]
    context = json.loads(
        specialist_call["messages"][-1]["content"][0]["text"]
        .removeprefix("<context>\n")
        .removesuffix("\n</context>")
    )
    days = {d["date"]: d for d in context["last_days"]}
    assert set(days) == {(TODAY - timedelta(days=n)).isoformat() for n in (1, 2, 3)}
    assert days[yesterday]["last_bite"] == "22:40"
    assert days[yesterday]["pillars"]["sleep"] == "missed"
    assert "Maggi" in days[yesterday]["evening_answer"]
    assert context["today_so_far"]["window"]["close"] == "20:30"
    assert context["numbers_visible"] is False
    # The contract is the stable, cached system prefix — not repeated in the volatile context.
    assert "program" not in context
    contract_block = specialist_call["system"][-1]
    assert "eating_window.target" in contract_block["text"]
    assert contract_block["cache_control"] == {"type": "ephemeral"}

    audit = admin_conn.execute(
        "SELECT actor, contract_clause, evidence FROM audit_log WHERE user_id = %s "
        "AND action = 'chat.reply'",
        (user,),
    ).fetchone()
    assert audit[0] == "agent:insight" and "sleep.consistent_bedtime" in audit[1]
    assert yesterday in audit[2]["cited_days"]
    roles = admin_conn.execute(
        "SELECT role FROM messages WHERE user_id = %s ORDER BY created_at", (user,)
    ).fetchall()
    assert [x[0] for x in roles] == ["user", "assistant"]
    assert (
        admin_conn.execute(
            "SELECT count(*) FROM checkpoints WHERE thread_id = %s", (str(user),)
        ).fetchone()[0]
        > 0
    )


def test_one_thread_across_telegram_and_pwa(phase1_app, make_token, admin_conn, model):
    client, services = phase1_app
    user = uuid.uuid4()
    headers = {"Authorization": f"Bearer {make_token(user)}"}
    client.get("/v1/me", headers=headers)
    admin_conn.execute(
        "INSERT INTO channel_identities (user_id, channel, external_id) "
        "VALUES (%s, 'telegram', '8080')",
        (user,),
    )
    model.will_route("nutrition_gut")
    model.will_answer("Two roti, dal and the bhindi you had on Thursday.")
    asyncio.run(
        handle_update(
            {
                "update_id": 1,
                "message": {
                    "message_id": 1,
                    "from": {"id": 8080},
                    "chat": {"id": 8080, "type": "private"},
                    "text": "what should I eat now?",
                },
            },
            services,
            _Gw(),
        )
    )

    model.will_route("nutrition_gut")
    model.will_answer("Yes, the dal from lunch works for dinner too.")
    r = client.post("/v1/chat", headers=headers, json={"message": "and for dinner?"})
    assert r.status_code == 200, r.text
    history = model.calls[-1]["messages"]
    assert history[0] == {"role": "user", "content": "what should I eat now?"}
    assert history[1]["content"].startswith("Two roti")
    channels = admin_conn.execute(
        "SELECT channel FROM messages WHERE user_id = %s ORDER BY created_at", (user,)
    ).fetchall()
    assert [c[0] for c in channels] == ["telegram", "telegram", "pwa", "pwa"]


def test_threads_never_mix(phase1_app, make_user, model):
    _, services = phase1_app
    alice, bob = make_user(), make_user()
    model.will_route("mind")
    model.will_answer("Alice-only reply.")
    run(ChatService(services).reply(alice, "my private worry", channel="pwa"))
    model.will_route("mind")
    model.will_answer("Hello Bob.")
    run(ChatService(services).reply(bob, "hi", channel="pwa"))
    assert "private worry" not in json.dumps(model.calls[-1]["messages"])


def test_red_flag_escalates_without_a_model(phase1_app, make_user, admin_conn):
    _, services = phase1_app
    services.anthropic = None
    user = make_user()
    r = run(ChatService(services).reply(user, "I fainted after my walk", channel="telegram"))
    assert r.escalated and "112" in r.text
    row = admin_conn.execute(
        "SELECT trigger_source, category, message_id FROM escalations WHERE user_id = %s", (user,)
    ).fetchone()
    assert row[0] == "red_flag_stopgap" and row[1] == "syncope" and row[2] is not None


def test_food_lookup_numbers_come_from_the_table(phase1_app, make_user):
    from swasthya_api.chat import PersonToolbox

    _, services = phase1_app
    box = PersonToolbox(services, make_user(), IST, numbers_visible=False)
    hits = run(box.food_lookup("chapati"))
    assert hits[0]["per_serving"]["protein_g"] == 2.4  # fixture: 40 g x 6 g/100 g
    assert "energy_kcal" not in hits[0]["per_serving"]  # Reset: no calories, even to the model


def test_fact_search(phase1_app, make_user, admin_conn):
    from swasthya_api.chat import PersonToolbox

    _, services = phase1_app
    user = make_user()
    admin_conn.execute(
        "INSERT INTO memory_facts (user_id, category, fact, source) VALUES "
        "(%s, 'trigger', 'Gas and bloating after spicy outside food', 't'), "
        "(%s, 'preference', 'Dislikes paneer', 't')",
        (user, user),
    )
    hits = run(PersonToolbox(services, user, IST, False).search_facts("spicy food"))
    assert [h["fact"] for h in hits] == ["Gas and bloating after spicy outside food"]


class _Gw:
    async def send_message(self, chat_id, text, *, reply_to=None, buttons=()):
        return 1

    async def download_file(self, file_id):
        return b""

    async def answer_callback(self, callback_id, text=None):
        pass

    async def typing(self, chat_id):
        pass
