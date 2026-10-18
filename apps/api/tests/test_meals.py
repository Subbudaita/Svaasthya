"""Meal logging end to end: HTTP → service → (scripted) perception → engine → DB."""

import uuid
from datetime import UTC, datetime, timedelta


def auth(make_token, user_id):
    return {"Authorization": f"Bearer {make_token(user_id)}"}


def signed_up(client, make_token):
    user_id = uuid.uuid4()
    headers = auth(make_token, user_id)
    assert client.get("/v1/me", headers=headers).status_code == 200
    return user_id, headers


def test_photo_log_is_perceived_computed_and_stored(
    phase1_app, make_token, model, item, jpeg_bytes, admin_conn
):
    client, _ = phase1_app
    user_id, headers = signed_up(client, make_token)
    model.will_see(
        item("chapati", "T001", 2, "chapati", 70, "grain"),
        item("moong dal", "T003", 1, "katori", 150, "protein", "medium"),
        item("plain curd", None, 1, "katori", 100, "dairy"),
    )
    r = client.post(
        "/v1/meals/photo", headers=headers, files={"photo": ("plate.jpg", jpeg_bytes, "image/jpeg")}
    )
    assert r.status_code == 201, r.text
    card = r.json()
    assert card["items"] == ["2 chapati", "1 katori moong dal", "1 katori plain curd"]
    assert card["unpriced"] == ["plain curd"]
    assert card["sentence"].startswith("No visible vegetable")
    # Reset (no contract yet): no numbers leave the server.
    assert card["numbers_visible"] is False and card["totals"] is None

    row = admin_conn.execute(
        "SELECT log_method, nutrients, perceived_items, items, photo_path, analysis_meta "
        "FROM meals WHERE id = %s",
        (card["meal_id"],),
    ).fetchone()
    method, nutrients, perceived, items, photo_path, meta = row
    assert method == "photo" and photo_path.startswith(str(user_id))
    # Stored numbers come from the table: 2 x 40 g chapati @ 6 g/100 g + 150 g dal @ 3 g/100 g.
    assert nutrients["protein_g"] == round(0.8 * 6 + 1.5 * 3, 2)
    assert items[0]["grams_source"] == "table_serving"
    assert items[1]["grams_source"] == "visual_estimate"
    assert perceived[1]["estimated_grams"] == 150
    assert meta["model"] == "claude-sonnet-5" and meta["input_tokens"] == 1200
    # The call was charged to this user's daily budget.
    used = admin_conn.execute(
        "SELECT tokens_used FROM token_usage WHERE user_id = %s", (user_id,)
    ).fetchone()[0]
    assert used == 1500


def test_numbers_appear_after_reset(phase1_app, make_token, model, item, admin_conn):
    client, _ = phase1_app
    user_id, headers = signed_up(client, make_token)
    admin_conn.execute(
        "INSERT INTO program_contracts (user_id, version, phase, body, generator_version) "
        "VALUES (%s, 1, 'rebuild', '{}', 'test')",
        (user_id,),
    )
    model.will_see(item("sambar", "T006", 1, "katori", 200, "protein"))
    card = client.post(
        "/v1/meals/text", headers=headers, json={"description": "a katori of sambar"}
    ).json()
    assert card["numbers_visible"] is True
    assert card["totals"]["protein_g"] == 6.8


def test_not_a_meal(phase1_app, make_token, model, jpeg_bytes):
    client, _ = phase1_app
    _, headers = signed_up(client, make_token)
    model.will_see(is_food=False, note="This looks like a laptop.")
    r = client.post(
        "/v1/meals/photo", headers=headers, files={"photo": ("x.jpg", jpeg_bytes, "image/jpeg")}
    )
    assert r.status_code == 422 and r.json()["detail"] == "not_a_meal"


def test_invalid_image(phase1_app, make_token):
    client, _ = phase1_app
    _, headers = signed_up(client, make_token)
    r = client.post(
        "/v1/meals/photo", headers=headers, files={"photo": ("x.jpg", b"nope", "image/jpeg")}
    )
    assert r.status_code == 422 and r.json()["detail"] == "invalid_image"


def test_no_api_key_is_503(phase1_app, make_token, jpeg_bytes):
    client, services = phase1_app
    services.anthropic = None
    _, headers = signed_up(client, make_token)
    r = client.post(
        "/v1/meals/photo", headers=headers, files={"photo": ("x.jpg", jpeg_bytes, "image/jpeg")}
    )
    assert r.status_code == 503


def test_same_as_yesterday_and_quick_picks_need_no_model(phase1_app, make_token, model, item):
    client, _ = phase1_app
    _, headers = signed_up(client, make_token)
    yesterday = (datetime.now(UTC) - timedelta(days=1)).isoformat()
    model.will_see(
        item("chapati", "T001", 3, "chapati", 100, "grain"),
        item("moong dal", "T003", 1, "katori", 150, "protein"),
    )
    first = client.post(
        "/v1/meals/text", headers=headers, json={"description": "3 roti dal", "eaten_at": yesterday}
    ).json()
    calls_before = len(model.calls)

    same = client.post("/v1/meals/same-as-yesterday", headers=headers)
    assert same.status_code == 201
    assert same.json()["items"] == first["items"] and same.json()["log_method"] == "repeat"

    picks = client.get("/v1/meals/quick-picks", headers=headers).json()
    assert picks[0]["count"] == 2 and picks[0]["label"] == "3 chapati, 1 katori moong dal"
    picked = client.post(f"/v1/meals/quick-picks/{picks[0]['meal_id']}", headers=headers)
    assert picked.status_code == 201 and picked.json()["log_method"] == "quick_pick"
    assert len(model.calls) == calls_before  # repeat logging never calls the model


def test_same_as_yesterday_with_nothing_to_repeat(phase1_app, make_token):
    client, _ = phase1_app
    _, headers = signed_up(client, make_token)
    assert client.post("/v1/meals/same-as-yesterday", headers=headers).status_code == 404


def test_fix_this_recomputes_and_remembers(phase1_app, make_token, model, item, admin_conn):
    client, _ = phase1_app
    user_id, headers = signed_up(client, make_token)
    model.will_see(item("plain dosa", "T004", 1, "dosa", 40, "grain"))
    card = client.post("/v1/meals/text", headers=headers, json={"description": "dosa"}).json()

    fixed = client.put(
        f"/v1/meals/{card['meal_id']}/items",
        headers=headers,
        json=[
            {
                "name": "masala dosa",
                "food_code": "T005",
                "quantity": 1,
                "unit": "dosa",
                "role": "grain",
            },
            {
                "name": "sambar",
                "food_code": "T006",
                "quantity": 1,
                "unit": "katori",
                "grams": 180,
                "role": "protein",
            },
        ],
    )
    assert fixed.status_code == 200
    assert fixed.json()["items"] == ["1 masala dosa", "1 katori sambar"]
    perceived, corrected = admin_conn.execute(
        "SELECT perceived_items, corrected_at FROM meals WHERE id = %s", (card["meal_id"],)
    ).fetchone()
    assert perceived[0]["name"] == "plain dosa"  # original perception kept
    assert corrected is not None
    fact = admin_conn.execute(
        "SELECT fact FROM memory_facts WHERE user_id = %s AND category = 'plate_correction'",
        (user_id,),
    ).fetchone()[0]
    assert "plain dosa" in fact and "masala dosa" in fact
    assert (
        admin_conn.execute(
            "SELECT count(*) FROM audit_log WHERE user_id = %s AND action = 'meal.corrected'",
            (user_id,),
        ).fetchone()[0]
        == 1
    )


def test_cannot_touch_another_users_meal(phase1_app, make_token, model, item):
    client, _ = phase1_app
    _, alice = signed_up(client, make_token)
    _, bob = signed_up(client, make_token)
    model.will_see(item("plain dosa", "T004", 1, "dosa", 40, "grain"))
    meal_id = client.post("/v1/meals/text", headers=alice, json={"description": "dosa"}).json()[
        "meal_id"
    ]
    r = client.put(
        f"/v1/meals/{meal_id}/items",
        headers=bob,
        json=[{"name": "x", "quantity": 1, "unit": "g", "grams": 1, "role": "other"}],
    )
    assert r.status_code == 404  # RLS hides it; it doesn't exist for Bob
    assert client.post(f"/v1/meals/quick-picks/{meal_id}", headers=bob).status_code == 404


def test_list_meals_for_a_day(phase1_app, make_token, model, item):
    client, _ = phase1_app
    _, headers = signed_up(client, make_token)
    model.will_see(item("chapati", "T001", 2, "chapati", 70, "grain"))
    client.post("/v1/meals/text", headers=headers, json={"description": "2 roti"})
    from datetime import date
    from zoneinfo import ZoneInfo

    today = datetime.now(ZoneInfo("Asia/Kolkata")).date().isoformat()
    meals = client.get(f"/v1/meals?day={today}", headers=headers).json()
    assert [m["items"] for m in meals] == [["2 chapati"]]
    assert isinstance(date.fromisoformat(today), date)
